from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3_assets as assets,
    aws_lambda as _lambda,
    aws_events as events,
    aws_events_targets as targets,
    CfnOutput,
    Duration,
)
from constructs import Construct
import os


class CHLBStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, key_name: str, mongodb_uri: str, instance_type: str = "t3.micro", **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Required Inputs ---
        if not key_name:
            raise Exception("❌ You must pass key_name via cdk context (--context key_name=...) or env CDK_KEY_NAME")

        if not mongodb_uri:
            raise Exception("❌ You must pass mongodb_uri via cdk context (--context mongodb_uri=...) or env MONGODB_URI")

        region = Stack.of(self).region

        # --- VPC and Security ---
        vpc = ec2.Vpc.from_lookup(self, "DefaultVPC", is_default=True)

        sg = ec2.SecurityGroup(self, "CHLBSecurityGroup",
                               vpc=vpc,
                               description="Allow SSH, LB ports, and backend ports",
                               allow_all_outbound=True)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH access")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(5000), "Load Balancer port")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8080), "Backend app port")

        # --- IAM Role for EC2 ---
        ec2_role = iam.Role(self, "EC2Role",
                            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"))
        ec2_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"))
        ec2_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy"))
        ec2_role.add_to_policy(iam.PolicyStatement(actions=["ec2:DescribeInstances"], resources=["*"]))

        instance_profile = iam.CfnInstanceProfile(self, "EC2InstanceProfile",
                                                  roles=[ec2_role.role_name],
                                                  instance_profile_name=f"{construct_id}-ec2-profile")

        # --- Scripts Packaging ---
        scripts_asset = assets.Asset(self, "ScriptsAsset", path=os.path.join(os.getcwd(), "scripts"))

        # --- Backend EC2 Instances ---
        backend_instances = []
        for i in range(3):
            ud_commands = [
                "#!/bin/bash -xe",
                "yum update -y || true",
                "amazon-linux-extras enable python3.8 -y || true",
                "yum install -y python3 unzip amazon-cloudwatch-agent -y",
                "pip3 install flask requests pymongo boto3",
                f"aws s3 cp s3://{scripts_asset.s3_bucket_name}/{scripts_asset.s3_object_key} /home/ec2-user/scripts.zip",
                "cd /home/ec2-user",
                "unzip -o scripts.zip -d scripts",

                # --- Configure CloudWatch Agent ---
                "cat <<'EOF' > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json",
                "{",
                "  \"logs\": {",
                "    \"logs_collected\": {",
                "      \"files\": {",
                "        \"collect_list\": [",
                f"          {{\"file_path\": \"/home/ec2-user/backend.log\", \"log_group_name\": \"CHLB_BackendLogs\", \"log_stream_name\": \"Backend-{i+1}\"}}",
                "        ]",
                "      }",
                "    }",
                "  }",
                "}",
                "EOF",
                "systemctl enable amazon-cloudwatch-agent",
                "systemctl start amazon-cloudwatch-agent",

                # --- Start backend app ---
                f"SERVER_NAME=Backend-{i+1} nohup python3 /home/ec2-user/scripts/backend_app.py > /home/ec2-user/backend.log 2>&1 &"
            ]

            instance = ec2.Instance(self, f"Backend{i+1}",
                                    instance_type=ec2.InstanceType(instance_type),
                                    machine_image=ec2.MachineImage.latest_amazon_linux(),
                                    vpc=vpc,
                                    security_group=sg,
                                    key_name=key_name,
                                    user_data=ec2.UserData.custom("\n".join(ud_commands)))
            instance.instance.add_property_override("IamInstanceProfile", instance_profile.ref)
            instance.node.default_child.add_override("Properties.Tags", [
                {"Key": "Name", "Value": f"Backend-{i+1}"},
                {"Key": "Role", "Value": "CHLBBackend"},
            ])
            backend_instances.append(instance)

        # --- Load Balancer EC2 Instance ---
        lb_ud_commands = [
            "#!/bin/bash -xe",
            "yum update -y || true",
            "amazon-linux-extras enable python3.8 -y || true",
            "yum install -y python3 unzip amazon-cloudwatch-agent -y",
            "pip3 install flask flask-socketio requests pymongo boto3",
            f"aws s3 cp s3://{scripts_asset.s3_bucket_name}/{scripts_asset.s3_object_key} /home/ec2-user/scripts.zip",
            "cd /home/ec2-user",
            "unzip -o scripts.zip -d scripts",

            # --- Configure CloudWatch Agent ---
            "cat <<'EOF' > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json",
            "{",
            "  \"logs\": {",
            "    \"logs_collected\": {",
            "      \"files\": {",
            "        \"collect_list\": [",
            "          {\"file_path\": \"/home/ec2-user/lb.log\", \"log_group_name\": \"CHLB_LBLogs\", \"log_stream_name\": \"LoadBalancer\"},",
            "          {\"file_path\": \"/home/ec2-user/mongo_bootstrap.log\", \"log_group_name\": \"CHLB_LBLogs\", \"log_stream_name\": \"MongoBootstrap\"}",
            "        ]",
            "      }",
            "    }",
            "  }",
            "}",
            "EOF",
            "systemctl enable amazon-cloudwatch-agent",
            "systemctl start amazon-cloudwatch-agent",

            # --- Write config.py dynamically ---
            "python3 - <<'PY'\n"
            "import boto3, json\n"
            f"region = '{region}'\n"
            "ec2 = boto3.client('ec2', region_name=region)\n"
            "res = ec2.describe_instances(Filters=[{'Name':'tag:Role','Values':['CHLBBackend']},{'Name':'instance-state-name','Values':['running']}])\n"
            "servers = []\n"
            "for r in res['Reservations']:\n"
            "    for i in r['Instances']:\n"
            "        ip = i.get('PrivateIpAddress')\n"
            "        name = next((t['Value'] for t in i.get('Tags',[]) if t['Key']=='Name'), 'Unknown')\n"
            "        servers.append({'name': name, 'ip': ip, 'port': 8080})\n"
            f"conf = f\"servers = {{json.dumps(servers)}}\\nMONGODB_URI = '{mongodb_uri}'\\nLB_PORT = 5000\\n\"\n"
            "with open('/home/ec2-user/scripts/config.py','w') as f:\n"
            "    f.write(conf)\n"
            "print('WROTE CONFIG:', conf)\n"
            "PY",
            "nohup python3 /home/ec2-user/scripts/mongo_bootstrap.py > /home/ec2-user/mongo_bootstrap.log 2>&1 &",
            "nohup python3 /home/ec2-user/scripts/lb.py > /home/ec2-user/lb.log 2>&1 &"
        ]

        lb_instance = ec2.Instance(self, "LBInstance",
                                   instance_type=ec2.InstanceType(instance_type),
                                   machine_image=ec2.MachineImage.latest_amazon_linux(),
                                   vpc=vpc,
                                   security_group=sg,
                                   key_name=key_name,
                                   user_data=ec2.UserData.custom("\n".join(lb_ud_commands)))
        lb_instance.instance.add_property_override("IamInstanceProfile", instance_profile.ref)
        lb_instance.node.default_child.add_override("Properties.Tags", [
            {"Key": "Name", "Value": "CHLB-LB"},
        ])

        # --- Permissions ---
        scripts_asset.grant_read(ec2_role)

        # --- Lambda Role ---
        lambda_role = iam.Role(self, "LambdaExecRole",
                               assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        lambda_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchReadOnlyAccess"))
        lambda_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3ReadOnlyAccess"))
        lambda_role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"))

        # --- Lambdas ---
        health_lambda = _lambda.Function(self, "HealthCheckerLambda",
                                         runtime=_lambda.Runtime.PYTHON_3_10,
                                         handler="health_checker.lambda_handler",
                                         code=_lambda.Code.from_asset(os.path.join(os.getcwd(), "scripts")),
                                         role=lambda_role,
                                         timeout=Duration.seconds(30),
                                         environment={
                                             "MONGODB_URI": mongodb_uri,
                                             "LB_IP": lb_instance.instance_private_ip
                                         })

        weight_lambda = _lambda.Function(self, "WeightCalculatorLambda",
                                         runtime=_lambda.Runtime.PYTHON_3_10,
                                         handler="weight_calculator.lambda_handler",
                                         code=_lambda.Code.from_asset(os.path.join(os.getcwd(), "scripts")),
                                         role=lambda_role,
                                         timeout=Duration.seconds(30),
                                         environment={
                                             "MONGODB_URI": mongodb_uri,
                                             "LB_IP": lb_instance.instance_private_ip
                                         })

        # --- Scheduler (Every 1 Minute) ---
        rule = events.Rule(self, "EveryMinuteRule",
                           schedule=events.Schedule.rate(Duration.minutes(1)))
        rule.add_target(targets.LambdaFunction(health_lambda))
        rule.add_target(targets.LambdaFunction(weight_lambda))

        # --- Outputs ---
        CfnOutput(self, "LBPublicIP", value=lb_instance.instance_public_ip)
        CfnOutput(self, "BackendPrivateIPs", value=",".join([inst.instance_private_ip for inst in backend_instances]))

import os
from datetime import datetime, timedelta
import boto3
import pymongo
import requests

MONGODB_URI = os.environ["MONGODB_URI"]
LB_IP = os.environ.get("LB_IP")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

client = pymongo.MongoClient(MONGODB_URI)
db = client["chlb"]
servers_col = db["servers"]

cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)


def get_instance_id_from_ip(ip):
    """Fallback lookup if instance_id missing."""
    try:
        resp = ec2.describe_instances(Filters=[{"Name": "private-ip-address", "Values": [ip]}])
        for r in resp.get("Reservations", []):
            for inst in r.get("Instances", []):
                return inst["InstanceId"]
    except Exception as e:
        print(f"[WARN] Could not resolve instance for {ip}: {e}")
    return None


def get_avg_cpu(instance_id, minutes=10):
    """Return average CPU over `minutes`. Returns 0.0 if no datapoint."""
    try:
        end = datetime.utcnow()
        start = end - timedelta(minutes=minutes)
        resp = cloudwatch.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=300,  # 5 min period
            Statistics=["Average"],
        )
        dps = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
        if not dps:
            print(f"[WARN] No CPU datapoints found for {instance_id} between {start} and {end}")
            return 0.0
        avg = float(dps[-1]["Average"])
        print(f"[INFO] Instance {instance_id} avg CPU = {avg:.2f}%")
        return avg
    except Exception as e:
        print(f"[ERROR] CloudWatch fetch failed for {instance_id}: {e}")
        return 0.0


def calculate_weight_from_cpu(avg_cpu):
    """Convert CPU -> weight. Higher CPU = lower weight."""
    if avg_cpu is None:
        return 1
    # Map 0% -> 10, 100% -> 1
    return int(max(1, min(10, round(10 - (avg_cpu / 10)))))


def lambda_handler(event=None, context=None):
    rebuild_needed = False
    servers = list(servers_col.find({}))

    for s in servers:
        iid = s.get("instance_id") or get_instance_id_from_ip(s.get("ip"))
        if not iid:
            print(f"[WARN] No instance_id found for {s.get('ip')}")
            continue

        avg_cpu = get_avg_cpu(iid, minutes=10)
        new_weight = calculate_weight_from_cpu(avg_cpu)

        update_fields = {
            "instance_id": iid,
            "last_checked": datetime.utcnow(),
            "cpu": avg_cpu,
            "weight": new_weight,
        }

        servers_col.update_one({"_id": s["_id"]}, {"$set": update_fields})

        if new_weight != s.get("weight", 1):
            rebuild_needed = True

    # Notify LB if needed
    if rebuild_needed and LB_IP:
        try:
            requests.post(f"http://{LB_IP}:5000/trigger_rebuild", timeout=3)
            print("[INFO] Rebuild triggered at LB.")
        except Exception as e:
            print("[WARN] Could not reach LB:", e)

    return {"status": "ok", "rebuild_needed": rebuild_needed}

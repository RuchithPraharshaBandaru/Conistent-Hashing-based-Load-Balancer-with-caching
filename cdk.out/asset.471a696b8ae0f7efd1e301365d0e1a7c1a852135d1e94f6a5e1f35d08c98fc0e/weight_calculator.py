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
    try:
        resp = ec2.describe_instances(Filters=[{"Name": "private-ip-address", "Values": [ip]}])
        for r in resp.get("Reservations", []):
            for inst in r.get("Instances", []):
                return inst["InstanceId"]
    except Exception as e:
        print(f"[WARN] Could not resolve instance for {ip}: {e}")
    return None


def get_metric(instance_id, metric_name, statistic="Average", minutes=10, namespace="AWS/EC2"):
    try:
        end = datetime.utcnow()
        start = end - timedelta(minutes=minutes)
        resp = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=300,
            Statistics=[statistic],
        )
        dps = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
        return float(dps[-1][statistic]) if dps else 0.0
    except Exception as e:
        print(f"[WARN] Metric {metric_name} failed for {instance_id}: {e}")
        return 0.0


def calculate_weight(cpu, net_in, net_out, disk_read, disk_write, status_failed):
    """Combine multiple metrics into a single weight."""
    if status_failed > 0:
        return 0  # unhealthy instance

    # Normalize all metrics roughly 0–100 range
    cpu_factor = max(0, min(100, cpu))
    net_factor = min(100, (net_in + net_out) / 1024**2)  # convert bytes to MB
    disk_factor = min(100, (disk_read + disk_write) / 100)

    # Weighted average: CPU is most important
    combined = 0.6 * cpu_factor + 0.25 * net_factor + 0.15 * disk_factor

    # Convert to load weight (inverse)
    weight = int(max(1, min(10, round(10 - (combined / 10)))))
    return weight


def lambda_handler(event=None, context=None):
    rebuild_needed = False
    servers = list(servers_col.find({}))

    for s in servers:
        iid = s.get("instance_id") or get_instance_id_from_ip(s.get("ip"))
        if not iid:
            print(f"[WARN] No instance_id found for {s.get('ip')}")
            continue

        cpu = get_metric(iid, "CPUUtilization")
        net_in = get_metric(iid, "NetworkIn")
        net_out = get_metric(iid, "NetworkOut")
        disk_read = get_metric(iid, "DiskReadOps")
        disk_write = get_metric(iid, "DiskWriteOps")
        status_failed = get_metric(iid, "StatusCheckFailed", statistic="Sum")

        new_weight = calculate_weight(cpu, net_in, net_out, disk_read, disk_write, status_failed)

        update_fields = {
            "instance_id": iid,
            "last_checked": datetime.utcnow(),
            "metrics": {
                "cpu": cpu,
                "net_in": net_in,
                "net_out": net_out,
                "disk_read": disk_read,
                "disk_write": disk_write,
                "status_failed": status_failed,
            },
            "weight": new_weight,
        }

        servers_col.update_one({"_id": s["_id"]}, {"$set": update_fields})

        if new_weight != s.get("weight", 1):
            rebuild_needed = True

    if rebuild_needed and LB_IP:
        try:
            requests.post(f"http://{LB_IP}:5000/trigger_rebuild", timeout=3)
            print("[INFO] Rebuild triggered at LB.")
        except Exception as e:
            print("[WARN] Could not reach LB:", e)

    return {"status": "ok", "rebuild_needed": rebuild_needed}

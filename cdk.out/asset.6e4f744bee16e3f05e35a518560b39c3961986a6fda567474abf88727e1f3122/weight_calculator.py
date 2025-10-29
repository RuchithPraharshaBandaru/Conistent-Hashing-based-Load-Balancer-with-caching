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
    except Exception:
        return None
    return None


def get_avg_cpu(instance_id, minutes=5):
    """Return average CPU over `minutes`. None on error/no datapoint."""
    try:
        end = datetime.utcnow()
        start = end - timedelta(minutes=minutes)
        resp = cloudwatch.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=60 * max(1, minutes),
            Statistics=["Average"],
        )
        dps = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
        if not dps:
            return None
        return float(dps[-1]["Average"])
    except Exception:
        return None


def calculate_weight_from_cpu(avg_cpu):
    """Convert CPU -> weight. Higher CPU = lower weight."""
    if avg_cpu is None:
        return None
    return int(max(1, min(10, round(10 - (avg_cpu / 10)))))


def lambda_handler(event=None, context=None):
    rebuild_needed = False
    servers = list(servers_col.find({}))

    for s in servers:
        iid = s.get("instance_id") or get_instance_id_from_ip(s.get("ip"))
        if not iid:
            print(f"[WARN] No instance_id found for {s.get('ip')}")
            continue

        avg_cpu = get_avg_cpu(iid, minutes=5)
        new_weight = calculate_weight_from_cpu(avg_cpu)

        update_fields = {
            "instance_id": iid,
            "last_checked": datetime.utcnow(),
            "cpu": avg_cpu,
        }

        if new_weight is not None:
            update_fields["weight"] = new_weight

        servers_col.update_one({"_id": s["_id"]}, {"$set": update_fields})

        # Compare weights for rebuild trigger
        if new_weight is not None and new_weight != s.get("weight", 1):
            rebuild_needed = True

    # notify LB to rebuild ring if needed
    if rebuild_needed and LB_IP:
        try:
            requests.post(f"http://{LB_IP}:5000/trigger_rebuild", timeout=3)
            print("[INFO] Rebuild triggered at LB.")
        except Exception as e:
            print("[WARN] Could not reach LB:", e)

    return {"status": "ok", "rebuild_needed": rebuild_needed}

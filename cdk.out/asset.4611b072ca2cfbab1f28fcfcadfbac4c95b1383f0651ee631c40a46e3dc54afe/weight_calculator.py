# scripts/weight_calculator.py
import os
from datetime import datetime, timedelta
import boto3
import pymongo
import requests

MONGODB_URI = os.environ["MONGODB_URI"]
LB_IP = os.environ.get("LB_IP")
client = pymongo.MongoClient(MONGODB_URI)
db = client["chlb"]
servers_col = db["servers"]

cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))

def get_avg_cpu(instance_id, minutes=10):
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
            Period=300,  # 5 min granularity
            Statistics=["Average"],
        )
        dps = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
        if not dps:
            print(f"[WARN] No datapoints for {instance_id} between {start} and {end}")
            return 0.0  # fallback to 0% CPU instead of None
        return float(dps[-1]["Average"])
    except Exception as e:
        print(f"[ERROR] CloudWatch metric fetch failed for {instance_id}: {e}")
        return 0.0


def lambda_handler(event, context):
    rebuild_needed = False
    servers = list(servers_col.find({}))
    for s in servers:
        iid = s.get("instance_id")
        if not iid:
            continue
        avg_cpu = get_avg_cpu(iid, minutes=5)
        if avg_cpu is None:
            continue
        # simple weight formula: higher CPU -> lower weight
        new_weight = max(1, int(10 - (avg_cpu / 10)))  # CPU 0 -> 10, CPU 100 -> 1
        if new_weight != s.get("weight", 1):
            servers_col.update_one({"_id": s["_id"]}, {"$set": {"weight": new_weight}})
            rebuild_needed = True
    if rebuild_needed and LB_IP:
        try:
            requests.post(f"http://{LB_IP}:5000/trigger_rebuild", timeout=3)
        except Exception:
            pass
    return {"status": "ok", "rebuild_needed": rebuild_needed}

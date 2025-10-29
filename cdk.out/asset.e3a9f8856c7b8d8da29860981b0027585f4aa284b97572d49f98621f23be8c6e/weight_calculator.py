# scripts/weight_calculator.py
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
        # take the latest datapoint average
        return float(dps[-1]["Average"])
    except Exception:
        return None


def calculate_weight_from_cpu(avg_cpu):
    """
    Convert CPU -> weight.
    - higher CPU -> lower weight
    - weight between 1 and 10
    This is tunable.
    """
    if avg_cpu is None:
        return None
    # Map 0% -> 10, 100% -> 1 (linear)
    w = int(max(1, min(10, round(10 - (avg_cpu / 10)))))
    return w


def lambda_handler(event, context):
    rebuild_needed = False
    servers = list(servers_col.find({}))
    for s in servers:
        iid = s.get("instance_id")  # expected to be set by your bootstrap or discovery
        if not iid:
            # no instance id — skip metrics update but ensure last_checked timestamp
            servers_col.update_one({"_id": s["_id"]}, {"$set": {"last_checked": datetime.utcnow()}})
            continue

        avg_cpu = get_avg_cpu(iid, minutes=5)
        new_weight = calculate_weight_from_cpu(avg_cpu)
        update_fields = {"last_checked": datetime.utcnow(), "cpu": avg_cpu}
        if new_weight is not None:
            update_fields["weight"] = new_weight

        # update DB only if difference (avoid noisy writes)
        changed = False
        current_weight = s.get("weight", 1)
        if new_weight is not None and new_weight != current_weight:
            changed = True

        try:
            servers_col.update_one({"_id": s["_id"]}, {"$set": update_fields})
        except Exception:
            # ignore transient DB errors
            pass

        if changed:
            rebuild_needed = True

    # notify LB to rebuild ring if needed
    if rebuild_needed and LB_IP:
        try:
            requests.post(f"http://{LB_IP}:5000/trigger_rebuild", timeout=3)
        except Exception:
            # ignore network failures
            pass

    return {"status": "ok", "rebuild_needed": rebuild_needed}

import os
import time
import boto3
import pymongo
from datetime import datetime, timedelta

# --- MongoDB Connection ---
MONGODB_URI = os.environ.get("MONGODB_URI") or "mongodb+srv://ruchithpraharshab23_db_user:Ruchith%402005@ccproject.waghehd.mongodb.net/chlb?retryWrites=true&w=majority&appName=CCPROJECT"
if not MONGODB_URI:
    raise RuntimeError("MongoDB URI not provided via env or config.py")

mongo = pymongo.MongoClient(MONGODB_URI)
db = mongo["chlb"]
servers_col = db["servers"]

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ec2 = boto3.client("ec2", region_name=AWS_REGION)
cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)


# ---------- Metrics Helpers ----------
def get_metric(instance_id, metric_name, statistic="Average", minutes=10, namespace="AWS/EC2"):
    """Fetches a CloudWatch metric safely."""
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
    """Converts multiple metrics into a load weight (1–10)."""
    if status_failed > 0:
        return 0  # unhealthy instance

    # Normalize roughly to 0–100
    cpu_factor = max(0, min(100, cpu))
    net_factor = min(100, (net_in + net_out) / (1024**2))  # bytes -> MB
    disk_factor = min(100, (disk_read + disk_write) / 100)

    combined = 0.6 * cpu_factor + 0.25 * net_factor + 0.15 * disk_factor
    weight = int(max(1, min(10, round(10 - (combined / 10)))))
    return weight


# ---------- Backend Discovery ----------
def discover_backends():
    """Find running EC2 instances tagged as CHLBBackend."""
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Role", "Values": ["CHLBBackend"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    servers = []
    for r in resp.get("Reservations", []):
        for inst in r.get("Instances", []):
            ip = inst.get("PrivateIpAddress")
            iid = inst.get("InstanceId")
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), iid)
            if not ip:
                continue

            # Pull recent metrics for initialization
            cpu = get_metric(iid, "CPUUtilization")
            net_in = get_metric(iid, "NetworkIn")
            net_out = get_metric(iid, "NetworkOut")
            disk_read = get_metric(iid, "DiskReadOps")
            disk_write = get_metric(iid, "DiskWriteOps")
            status_failed = get_metric(iid, "StatusCheckFailed", statistic="Sum")

            weight = calculate_weight(cpu, net_in, net_out, disk_read, disk_write, status_failed)

            servers.append({
                "name": name,
                "ip": ip,
                "port": 8080,
                "status": "HEALTHY" if status_failed == 0 else "UNHEALTHY",
                "weight": weight,
                "load_count": 0,
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
            })
    return servers


# ---------- Bootstrap ----------
def bootstrap():
    print("[BOOT] Clearing previous server data from MongoDB...")
    try:
        delete_result = servers_col.delete_many({})
        print(f"[BOOT] -> Deleted {delete_result.deleted_count} old server documents.")
    except Exception as e:
        print(f"[BOOT] -> Error clearing collection: {e}")

    servers = discover_backends()

    for s in servers:
        existing = servers_col.find_one({"instance_id": s["instance_id"]})
        if existing:
            servers_col.update_one({"_id": existing["_id"]}, {"$set": s})
            print(f"[BOOT] Updated {s['name']} ({s['ip']}) weight={s['weight']}")
        else:
            servers_col.insert_one(s)
            print(f"[BOOT] Inserted {s['name']} ({s['ip']}) weight={s['weight']}")

    print(f"[BOOT] Done. Found {len(servers)} backends.")


# ---------- Entry ----------
if __name__ == "__main__":
    tries = 0
    while tries < 5:
        try:
            bootstrap()
            break
        except Exception as e:
            print("[BOOT] Error:", e)
            tries += 1
            time.sleep(5)

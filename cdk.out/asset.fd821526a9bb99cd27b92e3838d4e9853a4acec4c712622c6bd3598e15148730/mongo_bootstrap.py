import os
import time
import boto3
import pymongo
from datetime import datetime

MONGODB_URI = "mongodb+srv://ruchithpraharshab23_db_user:Ruchith%402005@ccproject.waghehd.mongodb.net/chlb?retryWrites=true&w=majority&appName=CCPROJECT"
if not MONGODB_URI:
    try:
        from config import MONGODB_URI as cfg_muri
        MONGODB_URI = cfg_muri
    except Exception:
        raise RuntimeError("MongoDB URI not provided via env or config.py")

mongo = pymongo.MongoClient(MONGODB_URI)
db = mongo["chlb"]
servers_col = db["servers"]

ec2 = boto3.client("ec2", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def discover_backends():
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Role", "Values": ["CHLBBackend"]},
            {"Name": "instance-state-name", "Values": ["running"]}
        ]
    )
    servers = []
    for r in resp["Reservations"]:
        for inst in r["Instances"]:
            ip = inst.get("PrivateIpAddress")
            iid = inst.get("InstanceId")
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), iid)
            if ip:
                servers.append({
                    "name": name,
                    "ip": ip,
                    "port": 8080,
                    "status": "HEALTHY",
                    "weight": 1,
                    "load_count": 0,
                    "cpu": 0.0,
                    "instance_id": iid,
                    "last_checked": datetime.utcnow()
                })
    return servers


def bootstrap():
    print("[BOOT] Clearing previous server data from MongoDB...")
    try:
        delete_result = servers_col.delete_many({})
        print(f"[BOOT]  -> Deleted {delete_result.deleted_count} old server documents.")
    except Exception as e:
        print(f"[BOOT]  -> Error clearing collection: {e}")

    servers = discover_backends()
    for s in servers:
        existing = servers_col.find_one({"instance_id": s["instance_id"]})
        if existing:
            servers_col.update_one({"_id": existing["_id"]}, {"$set": s})
            print("[BOOT] updated", s["ip"])
        else:
            servers_col.insert_one(s)
            print("[BOOT] inserted", s["ip"])
    print("[BOOT] Done. Found", len(servers), "backends.")


if __name__ == "__main__":
    tries = 0
    while tries < 5:
        try:
            bootstrap()
            break
        except Exception as e:
            print("[BOOT] error:", e)
            tries += 1
            time.sleep(5)

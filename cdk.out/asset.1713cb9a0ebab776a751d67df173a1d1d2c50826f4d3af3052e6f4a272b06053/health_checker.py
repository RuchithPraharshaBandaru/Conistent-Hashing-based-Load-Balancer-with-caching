# scripts/health_checker.py
import os
import requests
import pymongo

MONGODB_URI = os.environ["MONGODB_URI"]
LB_IP = os.environ.get("LB_IP")  # LB private ip expected in Lambda env
client = pymongo.MongoClient(MONGODB_URI)
db = client["chlb"]
servers_col = db["servers"]

def lambda_handler(event, context):
    rebuild_needed = False
    servers = list(servers_col.find({}))
    for s in servers:
        ip = s.get("ip")
        port = s.get("port", 8080)
        try:
            r = requests.get(f"http://{ip}:{port}/health", timeout=3)
            new_status = "HEALTHY" if r.status_code == 200 else "UNHEALTHY"
        except Exception:
            new_status = "UNHEALTHY"
        if new_status != s.get("status"):
            servers_col.update_one({"_id": s["_id"]}, {"$set": {"status": new_status}})
            rebuild_needed = True
    if rebuild_needed and LB_IP:
        try:
            requests.post(f"http://{LB_IP}:5000/trigger_rebuild", timeout=3)
        except Exception:
            pass
    return {"status": "ok", "rebuild_needed": rebuild_needed}

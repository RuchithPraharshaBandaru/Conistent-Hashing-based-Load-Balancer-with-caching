# scripts/lb.py
import os
import threading
import time
import bisect
import requests
from hashlib import md5
from flask import Flask, jsonify, request
from flask_socketio import SocketIO
import pymongo
from flask_cors import CORS
import boto3
from datetime import datetime

# Config
LB_PORT = int(os.environ.get("LB_PORT", 5000))
MONGODB_URI = os.environ.get("MONGODB_URI") or None
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

if not MONGODB_URI:
    # fallback: try to import generated config (CDK user-data should create it)
    try:
        from config import MONGODB_URI as MONGODB_URI2
        MONGODB_URI = MONGODB_URI2
    except Exception:
        raise RuntimeError("MONGODB_URI not set in env or config.py")

# Flask + SocketIO
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# MongoDB client
mongo = pymongo.MongoClient(MONGODB_URI)
db = mongo["chlb"]
servers_col = db["servers"]

# CloudWatch client (optional, requires IAM)
try:
    cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
except Exception:
    cloudwatch = None


# Consistent hash ring implementation with weighted vnodes
class ConsistentHashRing:
    def __init__(self, vnodes_per_weight=10):
        self.ring = {}           # hash_int -> server_doc
        self.sorted_keys = []    # sorted list of hash_ints
        self.vnodes_per_weight = vnodes_per_weight

    def _hash(self, key):
        return int(md5(key.encode()).hexdigest(), 16)

    def build(self):
        self.ring.clear()
        self.sorted_keys.clear()
        servers = list(servers_col.find({"status": "HEALTHY"}))
        if not servers:
            return
        for s in servers:
            weight = max(1, int(s.get("weight", 1)))
            vcount = weight * self.vnodes_per_weight
            for i in range(vcount):
                vnode_key = f"{s['name']}-{i}"
                h = self._hash(vnode_key)
                self.ring[h] = s
                self.sorted_keys.append(h)
        self.sorted_keys.sort()

    def get_server(self, key):
        if not self.sorted_keys:
            return None
        h = self._hash(key)
        idx = bisect.bisect(self.sorted_keys, h) % len(self.sorted_keys)
        return self.ring[self.sorted_keys[idx]]

    def vnode_snapshot(self):
        """
        Return list of vnodes for visualization:
        each vnode -> {'hash': h, 'server': server_name, 'angle': 0-360}
        """
        snapshot = []
        if not self.sorted_keys:
            return snapshot
        # Normalize hash -> angle (0..360)
        for h in self.sorted_keys:
            s = self.ring.get(h)
            name = s.get("name") if s else "unknown"
            angle = (h % 360)  # simple mapping
            snapshot.append({"hash": h, "server": name, "angle": angle})
        return snapshot


lb_ring = ConsistentHashRing()


def rebuild_ring():
    lb_ring.build()
    broadcast_state()
    print("[LB] Ring rebuilt with", len(lb_ring.sorted_keys), "vnode positions")


def periodic_rebuild(interval=30):
    while True:
        try:
            rebuild_ring()
        except Exception as e:
            print("[LB] rebuild error:", e)
        time.sleep(interval)


def fetch_cloudwatch_cpu(instance_id, minutes=5):
    """Return latest CPU average datapoint (or None)."""
    if cloudwatch is None or instance_id is None:
        return None
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


def broadcast_state():
    """
    Build an enriched state object and emit via socketio.
    Also used as the HTTP response for /_internal/state.
    """
    docs = list(servers_col.find({}))
    servers = []
    requests_hist = []
    for s in docs:
        name = s.get("name")
        ip = s.get("ip")
        port = s.get("port", 8080)
        status = s.get("status")
        weight = int(s.get("weight", 1))
        load = int(s.get("load_count", 0))
        instance_id = s.get("instance_id")  # optional
        cpu = None
        if instance_id:
            # best-effort CloudWatch fetch (may require IAM)
            try:
                cpu = fetch_cloudwatch_cpu(instance_id, minutes=5)
            except Exception:
                cpu = None

        servers.append({
            "name": name,
            "ip": ip,
            "port": port,
            "status": status,
            "weight": weight,
            "vnodes": max(1, weight) * lb_ring.vnodes_per_weight,
            "load_count": load,
            "instance_id": instance_id,
            "cpu": cpu,
        })
        requests_hist.append({"name": name, "load_count": load})

    # vnode snapshot for visualization
    vnode_list = lb_ring.vnode_snapshot()
    state = {
        "timestamp": time.time(),
        "servers": servers,
        "vnodes": vnode_list,
        "ring_size": len(lb_ring.sorted_keys),
        "requests_histogram": requests_hist,
    }

    # emit to websocket clients
    try:
        socketio.emit("state_update", state)
    except Exception:
        pass

    return state


# Start periodic rebuild thread
thread = threading.Thread(target=periodic_rebuild, args=(30,), daemon=True)
thread.start()


# Routes
@app.route("/<key>", methods=["GET"])
def route_key(key):
    server = lb_ring.get_server(key)
    if not server:
        return jsonify({"error": "no healthy servers available"}), 503
    ip = server.get("ip")
    port = server.get("port", 8080)
    try:
        url = f"http://{ip}:{port}/{key}"
        resp = requests.get(url, timeout=5)
        servers_col.update_one({"_id": server["_id"]}, {"$inc": {"load_count": 1}})
        broadcast_state()
        return jsonify(resp.json())
    except Exception as e:
        print(f"[LB] error proxying to {ip}:{port} ->", e)
        servers_col.update_one({"_id": server["_id"]}, {"$set": {"status": "UNHEALTHY"}})
        rebuild_ring()
        return jsonify({"error": "upstream unreachable"}), 502


@app.route("/trigger_rebuild", methods=["POST"])
def trigger_rebuild():
    try:
        rebuild_ring()
        return jsonify({"status": "rebuild triggered"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/_internal/state", methods=["GET"])
def internal_state():
    """
    Return enriched JSON state for dashboard:
    {
      timestamp, servers[], vnodes[], ring_size, requests_histogram[]
    }
    """
    state = broadcast_state()
    return jsonify(state)


if __name__ == "__main__":
    try:
        rebuild_ring()
    except Exception as e:
        print("[LB] initial rebuild failed:", e)
    # socketio.run binds to 0.0.0.0 so external requests can hit it
    socketio.run(app, host="0.0.0.0", port=LB_PORT, allow_unsafe_werkzeug=True)

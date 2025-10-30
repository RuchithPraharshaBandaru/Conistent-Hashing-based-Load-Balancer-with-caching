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
from datetime import datetime

# Config
LB_PORT = int(os.environ.get("LB_PORT", 5000))
MONGODB_URI = os.environ.get("MONGODB_URI") or None
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

if not MONGODB_URI:
    try:
        from config import MONGODB_URI as MONGODB_URI2
        MONGODB_URI = MONGODB_URI2
    except Exception:
        raise RuntimeError("MONGODB_URI not set in env or config.py")

# Flask + SocketIO setup
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# MongoDB client
mongo = pymongo.MongoClient(MONGODB_URI)
db = mongo["chlb"]
servers_col = db["servers"]


# =========================
# Consistent Hashing System
# =========================
class ConsistentHashRing:
    def __init__(self, vnodes_per_weight=10):
        self.ring = {}
        self.sorted_keys = []
        self.vnodes_per_weight = vnodes_per_weight

    def _hash(self, key):
        return int(md5(key.encode()).hexdigest(), 16)

    def build(self):
        """Rebuild ring from healthy servers."""
        self.ring.clear()
        self.sorted_keys.clear()
        servers = list(servers_col.find({"status": "HEALTHY"}))
        if not servers:
            print("[LB] No healthy servers found during rebuild.")
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
        print(f"[LB] Ring built with {len(self.sorted_keys)} vnodes across {len(servers)} servers.")

    def get_server(self, key):
        """Return backend responsible for given key."""
        if not self.sorted_keys:
            return None
        h = self._hash(key)
        idx = bisect.bisect(self.sorted_keys, h) % len(self.sorted_keys)
        return self.ring[self.sorted_keys[idx]]

    def vnode_snapshot(self):
        """For dashboard visualization."""
        snapshot = []
        if not self.sorted_keys:
            return snapshot
        for h in self.sorted_keys:
            s = self.ring.get(h)
            snapshot.append({
                "hash": h,
                "server": s.get("name", "unknown"),
                "angle": h % 360
            })
        return snapshot


lb_ring = ConsistentHashRing()


# =====================
# Load Balancer Methods
# =====================
def rebuild_ring():
    lb_ring.build()
    broadcast_state()
    print("[LB] Ring rebuilt at", datetime.utcnow().isoformat())


def periodic_rebuild(interval=60):
    """Optional background refresh to pick up DB changes."""
    while True:
        try:
            rebuild_ring()
        except Exception as e:
            print("[LB] rebuild error:", e)
        time.sleep(interval)


def broadcast_state():
    """Emit current state via SocketIO and return JSON object."""
    docs = list(servers_col.find({}))
    servers = []
    requests_hist = []

    for s in docs:
        metrics = s.get("metrics", {})
        servers.append({
            "name": s.get("name"),
            "ip": s.get("ip"),
            "port": s.get("port", 8080),
            "status": s.get("status"),
            "weight": s.get("weight", 1),
            "vnodes": max(1, s.get("weight", 1)) * lb_ring.vnodes_per_weight,
            "load_count": s.get("load_count", 0),
            "instance_id": s.get("instance_id"),
            "metrics": metrics,
            "last_checked": s.get("last_checked"),
        })
        requests_hist.append({
            "name": s.get("name"),
            "load_count": s.get("load_count", 0)
        })

    vnode_list = lb_ring.vnode_snapshot()
    state = {
        "timestamp": time.time(),
        "servers": servers,
        "vnodes": vnode_list,
        "ring_size": len(lb_ring.sorted_keys),
        "requests_histogram": requests_hist,
    }

    try:
        socketio.emit("state_update", state)
    except Exception:
        pass

    return state


# =====================
# Flask Route Handlers
# =====================
@app.route("/<key>", methods=["GET"])
def route_key(key):
    """Proxy client requests based on consistent hashing."""
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
        print(f"[LB] Error proxying to {ip}:{port}: {e}")
        servers_col.update_one({"_id": server["_id"]}, {"$set": {"status": "UNHEALTHY"}})
        rebuild_ring()
        return jsonify({"error": "upstream unreachable"}), 502


@app.route("/trigger_rebuild", methods=["POST"])
def trigger_rebuild():
    """Triggered by weight-calculator Lambda when weights change."""
    try:
        rebuild_ring()
        return jsonify({"status": "rebuild triggered"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/_internal/state", methods=["GET"])
def internal_state():
    """Expose current ring and server info for dashboard or monitoring."""
    return jsonify(broadcast_state())


# =====================
# Startup
# =====================
if __name__ == "__main__":
    try:
        rebuild_ring()
    except Exception as e:
        print("[LB] Initial ring build failed:", e)

    # Optional background refresh every 60s
    threading.Thread(target=periodic_rebuild, args=(60,), daemon=True).start()

    socketio.run(app, host="0.0.0.0", port=LB_PORT, allow_unsafe_werkzeug=True)

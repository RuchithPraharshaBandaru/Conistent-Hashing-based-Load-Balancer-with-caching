import os
import threading
import time
import bisect
import requests
from hashlib import md5
from flask import Flask, jsonify, request
from flask_socketio import SocketIO
import pymongo

# Config
LB_PORT = int(os.environ.get("LB_PORT", 5000))
MONGODB_URI = "mongodb+srv://ruchithpraharshab23_db_user:Ruchith%402005@ccproject.waghehd.mongodb.net/chlb" # CDK writes this into config.py on EC2

if not MONGODB_URI:
    # fallback: try to import generated config (CDK user-data should create it)
    try:
        from config import MONGODB_URI as MONGODB_URI2   # <-- FIXED
        MONGODB_URI = MONGODB_URI2
    except Exception:
        raise RuntimeError("MONGODB_URI not set in env or config.py")

# Flask + SocketIO
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# MongoDB client
mongo = pymongo.MongoClient(MONGODB_URI)
db = mongo["chlb"]
servers_col = db["servers"]

# Consistent hash ring implementation with weighted vnodes
class ConsistentHashRing:
    def __init__(self, vnodes_per_weight=10):
        self.ring = {}
        self.sorted_keys = []
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

def broadcast_state():
    docs = list(servers_col.find({}))
    state = []
    for s in docs:
        state.append({
            "name": s.get("name"),
            "ip": s.get("ip"),
            "port": s.get("port", 8080),
            "status": s.get("status"),
            "weight": s.get("weight", 1),
            "vnodes": max(1, int(s.get("weight",1))) * lb_ring.vnodes_per_weight,
            "load": s.get("load_count", 0)
        })
    socketio.emit("state_update", {"servers": state})

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
    docs = list(servers_col.find({}))
    return jsonify([{
        "name": s.get("name"),
        "ip": s.get("ip"),
        "port": s.get("port"),
        "status": s.get("status"),
        "weight": s.get("weight",1),
        "load_count": s.get("load_count", 0)
    } for s in docs])

if __name__ == "__main__":
    try:
        rebuild_ring()
    except Exception as e:
        print("[LB] initial rebuild failed:", e)
    socketio.run(app, host="0.0.0.0", port=LB_PORT)

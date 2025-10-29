# scripts/backend_app.py
from flask import Flask, jsonify, request
import os

app = Flask(__name__)

SERVER_NAME = os.environ.get("SERVER_NAME", "BackendX")

@app.route("/", defaults={"key": None})
@app.route("/<key>")
def serve(key):
    return jsonify({
        "server": SERVER_NAME,
        "key": key,
        "message": f"Served by {SERVER_NAME}"
    })

@app.route("/health")
def health():
    # Simple health endpoint for health checker Lambda
    return jsonify({"status": "ok", "server": SERVER_NAME}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

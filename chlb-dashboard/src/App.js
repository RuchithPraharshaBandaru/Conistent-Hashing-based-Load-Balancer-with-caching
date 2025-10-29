import React, { useEffect, useState } from "react";

function App() {
  const [data, setData] = useState(null);
  const LB_ENDPOINT = "http://54.237.247.138:5000/_internal/state"; // 🔁 replace <LB_IP>

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await fetch(LB_ENDPOINT);
        const json = await res.json();
        setData(json);
      } catch (err) {
        console.error("Error fetching state:", err);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 5000); // auto refresh every 5s
    return () => clearInterval(interval);
  }, []);

  if (!data) return <p>Loading load balancer data...</p>;

  const { servers, vnodes, requests_histogram, ring_size } = data;

  return (
    <div style={{ fontFamily: "sans-serif", padding: "20px", background: "#f5f5f5" }}>
      <h1 style={{ textAlign: "center" }}>🌀 Consistent Hashing Dashboard</h1>
      <h3 style={{ textAlign: "center", color: "gray" }}>LB Endpoint: {LB_ENDPOINT}</h3>

      {/* ---- Server Table ---- */}
      <h2>Servers</h2>
      <table style={{ borderCollapse: "collapse", width: "100%", background: "white" }}>
        <thead>
          <tr style={{ background: "#ddd" }}>
            <th>Name</th>
            <th>IP</th>
            <th>Port</th>
            <th>Status</th>
            <th>Load Count</th>
            <th>vNodes</th>
            <th>Weight</th>
          </tr>
        </thead>
        <tbody>
          {servers.map((srv, idx) => (
            <tr key={idx}>
              <td>{srv.name}</td>
              <td>{srv.ip}</td>
              <td>{srv.port}</td>
              <td>{srv.status}</td>
              <td>{srv.load_count}</td>
              <td>{srv.vnodes}</td>
              <td>{srv.weight}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* ---- Requests Histogram ---- */}
      <h2 style={{ marginTop: "40px" }}>Request Distribution</h2>
      <div style={{ display: "flex", gap: "10px", alignItems: "flex-end" }}>
        {requests_histogram.map((r, idx) => (
          <div key={idx} style={{ textAlign: "center" }}>
            <div
              style={{
                width: "50px",
                height: `${r.load_count * 10 + 10}px`,
                background: "#4caf50",
                marginBottom: "5px",
              }}
            ></div>
            <div style={{ fontSize: "14px" }}>{r.name}</div>
          </div>
        ))}
      </div>

      {/* ---- Ring Visualization ---- */}
      <h2 style={{ marginTop: "40px" }}>Hash Ring (vNodes: {ring_size})</h2>
      <div
        style={{
          position: "relative",
          width: "400px",
          height: "400px",
          margin: "40px auto",
          borderRadius: "50%",
          border: "3px solid #2196f3",
          background: "#e3f2fd",
        }}
      >
        {vnodes.map((v, i) => {
          const angle = (v.angle / 360) * 2 * Math.PI;
          const radius = 180;
          const x = 200 + radius * Math.cos(angle);
          const y = 200 + radius * Math.sin(angle);
          return (
            <div
              key={i}
              title={`${v.server} (angle: ${v.angle})`}
              style={{
                position: "absolute",
                left: `${x}px`,
                top: `${y}px`,
                transform: "translate(-50%, -50%)",
                width: "10px",
                height: "10px",
                borderRadius: "50%",
                background:
                  v.server === "Backend-1"
                    ? "#f44336"
                    : v.server === "Backend-2"
                    ? "#4caf50"
                    : "#2196f3",
              }}
            ></div>
          );
        })}
      </div>

      <p style={{ textAlign: "center", color: "gray" }}>
        Updated: {new Date().toLocaleTimeString()}
      </p>
    </div>
  );
}

export default App;

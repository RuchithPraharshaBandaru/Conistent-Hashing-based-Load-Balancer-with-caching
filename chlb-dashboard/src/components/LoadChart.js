import React from "react";

export default function LoadChart({ servers }) {
  const maxLoad = Math.max(...servers.map(s => s.load_count || 0), 1);

  return (
    <div className="section">
      <h2>Request Load per Server</h2>
      <div className="chart">
        {servers.map((s, i) => (
          <div key={i} className="bar-group">
            <div
              className="bar"
              style={{
                height: `${(s.load_count / maxLoad) * 200}px`,
                backgroundColor: s.status === "HEALTHY" ? "#4CAF50" : "#F44336",
              }}
            ></div>
            <p className="label">{s.name}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

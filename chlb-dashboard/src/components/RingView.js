import React from "react";

export default function RingView({ servers }) {
  const radius = 120;
  const angleStep = (2 * Math.PI) / servers.length;

  return (
    <div className="section">
      <h2>Consistent Hash Ring (Simplified View)</h2>
      <div className="ring">
        {servers.map((s, i) => {
          const angle = i * angleStep;
          const x = radius * Math.cos(angle);
          const y = radius * Math.sin(angle);
          return (
            <div
              key={i}
              className="node"
              style={{
                transform: `translate(${x}px, ${y}px)`,
                backgroundColor: s.status === "HEALTHY" ? "#4CAF50" : "#F44336",
              }}
              title={`${s.name} (${s.weight})`}
            >
              {s.name}
            </div>
          );
        })}
      </div>
    </div>
  );
}

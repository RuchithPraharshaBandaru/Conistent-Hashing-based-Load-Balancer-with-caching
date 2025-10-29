import React, { useEffect, useState } from "react";

const ServerTable = () => {
  const [servers, setServers] = useState([]); // ✅ initialize as empty array
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("http://localhost:5000/servers")
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          setServers(data);
        } else if (Array.isArray(data.servers)) {
          setServers(data.servers);
        } else {
          setError("Invalid server data format");
          console.error("Expected array, got:", data);
        }
      })
      .catch((err) => {
        console.error(err);
        setError("Failed to fetch servers");
      });
  }, []);

  return (
    <div style={{ padding: "20px" }}>
      <h1>Server Load Balancer Dashboard</h1>
      {error && <p style={{ color: "red" }}>{error}</p>}
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          marginTop: "20px",
        }}
      >
        <thead>
          <tr style={{ backgroundColor: "#f0f0f0" }}>
            <th style={thStyle}>Server ID</th>
            <th style={thStyle}>Current Load</th>
            <th style={thStyle}>Status</th>
          </tr>
        </thead>
        <tbody>
          {servers.map((server) => (
            <tr key={server.id}>
              <td style={tdStyle}>{server.id}</td>
              <td style={tdStyle}>{server.load}%</td>
              <td style={tdStyle}>
                {server.status === "active" ? (
                  <span style={{ color: "green" }}>🟢 Active</span>
                ) : (
                  <span style={{ color: "red" }}>🔴 Down</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const thStyle = {
  border: "1px solid #ddd",
  padding: "10px",
  textAlign: "left",
  fontWeight: "bold",
};

const tdStyle = {
  border: "1px solid #ddd",
  padding: "10px",
};

export default ServerTable;

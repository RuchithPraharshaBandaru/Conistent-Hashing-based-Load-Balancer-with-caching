import React from "react";
import "./ServerTable.css";

function ServerTable({ servers }) {
  if (!servers || servers.length === 0) {
    return <p>No servers registered yet.</p>;
  }

  return (
    <table className="server-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>IP</th>
          <th>Port</th>
          <th>Status</th>
          <th>Weight</th>
          <th>Load Count</th>
        </tr>
      </thead>
      <tbody>
        {servers.map((s, idx) => (
          <tr key={idx} className={s.status === "active" ? "active" : "inactive"}>
            <td>{s.name}</td>
            <td>{s.ip}</td>
            <td>{s.port}</td>
            <td>{s.status}</td>
            <td>{s.weight}</td>
            <td>{s.load_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default ServerTable;

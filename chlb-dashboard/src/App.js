import React, { useEffect, useState } from "react";
import "./App.css";
import ServerTable from "./components/ServerTable";

function App() {
  const [lbEndpoint, setLbEndpoint] = useState("");
  const [servers, setServers] = useState([]);
  const [loading, setLoading] = useState(true);

  const API_URL = "http://54.224.142.41/_internal/state"; // replace with your LB IP

  useEffect(() => {
    async function fetchData() {
      try {
        const res = await fetch(API_URL);
        const data = await res.json();
        setServers(data);
        setLbEndpoint(API_URL.replace("/_internal/state", ""));
      } catch (error) {
        console.error("Error fetching data:", error);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="App">
      <h1>🌀 Consistent Hashing Load Balancer Dashboard</h1>
      <p><strong>LB Endpoint:</strong> {lbEndpoint || "Loading..."}</p>

      {loading ? (
        <p className="loading">Fetching server data...</p>
      ) : (
        <ServerTable servers={servers} />
      )}
    </div>
  );
}

export default App;

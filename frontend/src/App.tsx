import { useEffect, useState } from "react";

type Health = { status: string; database: string; app: string };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then(async (res) => {
        if (!res.ok) throw new Error(`Backend responded with status ${res.status}`);
        return res.json() as Promise<Health>;
      })
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "sans-serif", padding: 24 }}>
      <h1>Pinessa Order-to-Delivery Copilot</h1>
      {!health && !error && <p>Checking backend...</p>}
      {health && (
        <p>
          Backend: {health.status.toUpperCase()} | Database: {health.database}
        </p>
      )}
      {error && <p style={{ color: "crimson" }}>Problem: {error}</p>}
    </main>
  );
}
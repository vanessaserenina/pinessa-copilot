import { useState } from "react";
import { errorMessage, useQuery } from "./api";
import type { Api } from "./api";

type Overview = {
  groups: Record<string, Record<string, number>>;
  notifications: { id: number; message: string; entity: string; entity_id: number }[];
};

export function Dashboard({ api }: { api: Api }) {
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const result = useQuery<Overview>(api, "/dashboard", revision);

  async function markRead(id: number) {
    setError("");
    try {
      await api(`/dashboard/notifications/${id}/read`, { method: "POST" });
      setRevision(r => r + 1);
    } catch (err) { setError(errorMessage(err)); }
  }

  return <section><div className="actions"><h1>Operations dashboard</h1>
    <button onClick={() => setRevision(r => r + 1)}>Refresh</button></div>
    {(error || result.error) && <p className="error" role="alert">{error || result.error}</p>}
    {!result.data && !result.error && <p role="status">Loading dashboard…</p>}
    {result.data && <><div className="dashboard-totals">{Object.entries(result.data.groups).map(([group, totals]) =>
      <section className="panel" key={group}><h2>{group}</h2>
        {Object.keys(totals).length ? <dl>{Object.entries(totals).sort(([a], [b]) => a.localeCompare(b)).map(([status, count]) =>
          <div className="dashboard-count" key={status}><dt>{status.replaceAll("_", " ")}</dt><dd>{count}</dd></div>)}</dl>
          : <p>No records.</p>}
      </section>)}</div>
      <section className="panel"><h2>Unread notifications</h2>
        {!result.data.notifications.length && <p>No unread notifications.</p>}
        {result.data.notifications.map(item => <div className="dashboard-count" key={item.id}>
          <p>{item.message} · {item.entity.replaceAll("_", " ")} #{item.entity_id}</p>
          <button onClick={() => markRead(item.id)}>Mark as read</button></div>)}</section></>}
  </section>;
}

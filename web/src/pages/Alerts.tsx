import { useCallback, useEffect, useState } from "react";
import { AlertEntry, api, Project, Rule } from "../api";
import { timeAgo } from "../lib";

export function Alerts({ project }: { project: Project }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [log, setLog] = useState<AlertEntry[]>([]);
  const [f, setF] = useState({ kind: "new_issue", channel: "webhook", webhook_url: "", email_to: "", threshold: 10, window_seconds: 60 });
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    api.rules(project.id).then(setRules).catch((e) => setErr(e.message));
    api.alerts(project.id).then(setLog).catch(() => {});
  }, [project.id]);
  useEffect(load, [load]);

  return (
    <section>
      <h2>Alert rules</h2>
      <p className="muted">A rule POSTs JSON to your webhook or emails you (server needs SMTP_* settings). <b>new_issue</b> fires when a new issue is created; <b>spike</b> fires when one issue gets ≥ threshold events within the window.</p>
      <form className="row" onSubmit={async (e) => {
        e.preventDefault();
        try { await api.createRule(project.id, { ...f, email_to: f.channel === "email" ? f.email_to : undefined }); setErr(""); load(); } catch (x) { setErr((x as Error).message); }
      }}>
        <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option>new_issue</option><option>spike</option></select>
        <select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}><option>webhook</option><option>email</option></select>
        {f.channel === "webhook"
          ? <input type="url" required placeholder="https://… webhook URL" value={f.webhook_url} onChange={(e) => setF({ ...f, webhook_url: e.target.value })} />
          : <input type="email" required placeholder="you@example.com" value={f.email_to} onChange={(e) => setF({ ...f, email_to: e.target.value })} />}
        {f.kind === "spike" && <>
          <input type="number" min={1} value={f.threshold} onChange={(e) => setF({ ...f, threshold: Number(e.target.value) })} title="threshold (events)" />
          <input type="number" min={1} value={f.window_seconds} onChange={(e) => setF({ ...f, window_seconds: Number(e.target.value) })} title="window (seconds)" />
        </>}
        <button>Add rule</button>
      </form>
      {err && <p className="err">{err}</p>}
      <table>
        <thead><tr><th>Kind</th><th>Condition</th><th>Target</th><th>Last fired</th><th></th></tr></thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id}><td>{r.kind}</td><td>{r.kind === "spike" ? `≥ ${r.threshold} in ${r.window_seconds}s` : "any new issue"}</td>
              <td><code>{r.channel === "email" ? r.email_to : r.webhook_url}</code></td><td>{timeAgo(r.last_fired)}</td>
              <td><button className="link" onClick={async () => { await api.deleteRule(r.id); load(); }}>delete</button></td></tr>))}
          {rules.length === 0 && <tr><td colSpan={5} className="muted">No rules yet.</td></tr>}
        </tbody>
      </table>
      <h3>Recent alerts</h3>
      <table>
        <thead><tr><th>When</th><th>Kind</th><th>Issue</th><th>Delivered</th><th>Detail</th></tr></thead>
        <tbody>
          {log.map((a) => (
            <tr key={a.id}><td>{timeAgo(a.fired_at)}</td><td>{a.kind}</td>
              <td><a href={`#/projects/${project.id}/issues/${a.issue_id}`}>#{a.issue_id}</a></td>
              <td>{a.delivered ? "yes" : "no"}</td><td className="muted">{a.detail}</td></tr>))}
          {log.length === 0 && <tr><td colSpan={5} className="muted">Nothing fired yet.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

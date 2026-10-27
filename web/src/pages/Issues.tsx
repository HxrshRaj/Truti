import { useEffect, useState } from "react";
import { api, Issue, Project } from "../api";
import { sparkPoints, timeAgo } from "../lib";

export function IssuesPage({ project }: { project: Project }) {
  const [items, setItems] = useState<Issue[]>([]);
  const [total, setTotal] = useState(0);
  const [f, setF] = useState({ query: "", status: "unresolved", environment: "", release: "", sort: "last_seen" });
  const [meta, setMeta] = useState<{ releases: string[]; environments: string[] }>({ releases: [], environments: [] });
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.releases(project.id).then((r) => setMeta({ releases: r.releases.map((x) => x.version), environments: r.environments })).catch(() => {});
  }, [project.id]);

  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      setLoading(true);
      api.issues(project.id, f).then((r) => { if (live) { setItems(r.items); setTotal(r.total); setErr(""); } })
        .catch((e) => live && setErr(e.message)).finally(() => live && setLoading(false));
    }, 200);
    return () => { live = false; clearTimeout(t); };
  }, [project.id, f]);

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  return (
    <section>
      <div className="filters">
        <input placeholder="Search title, type, culprit…" value={f.query} onChange={set("query")} />
        <select value={f.status} onChange={set("status")}>
          <option value="">All statuses</option><option>unresolved</option><option>resolved</option><option>ignored</option>
        </select>
        <select value={f.environment} onChange={set("environment")}>
          <option value="">All environments</option>{meta.environments.map((e) => <option key={e}>{e}</option>)}
        </select>
        <select value={f.release} onChange={set("release")}>
          <option value="">All releases</option>{meta.releases.map((e) => <option key={e}>{e}</option>)}
        </select>
        <select value={f.sort} onChange={set("sort")}>
          <option value="last_seen">Last seen</option><option value="count">Events</option><option value="first_seen">First seen</option>
        </select>
      </div>
      {err && <p className="err">{err}</p>}
      <table>
        <thead><tr><th>Issue</th><th>Last 24h</th><th className="num">Events</th><th>Last seen</th><th>First seen</th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id} className={i.status !== "unresolved" ? "dim" : ""}>
              <td>
                <a href={`#/projects/${project.id}/issues/${i.id}`}><span className={`lvl ${i.level}`}>{i.level}</span> {i.title}</a>
                <div className="muted">{i.culprit}{i.last_release ? ` · ${i.last_release}` : ""}{i.status !== "unresolved" ? ` · ${i.status}` : ""}</div>
              </td>
              <td><svg width="96" height="24" role="img" aria-label="events per hour, last 24 hours">
                <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={sparkPoints(i.sparkline ?? [])} /></svg></td>
              <td className="num">{i.count}</td>
              <td title={i.last_seen}>{timeAgo(i.last_seen)}</td>
              <td title={i.first_seen}>{timeAgo(i.first_seen)}</td>
            </tr>
          ))}
          {!loading && items.length === 0 && <tr><td colSpan={5} className="muted">No issues match. Send an error with the DSN from “Projects &amp; DSN”.</td></tr>}
        </tbody>
      </table>
      <p className="muted">{total} issue{total === 1 ? "" : "s"}</p>
    </section>
  );
}

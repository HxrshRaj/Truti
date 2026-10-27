import { useEffect, useState } from "react";
import { api, Frame, IssueDetail, TrutiEvent } from "../api";
import { timeAgo } from "../lib";

function FrameView({ f }: { f: Frame }) {
  const [open, setOpen] = useState(f.in_app);
  const has = f.context_line !== undefined && f.context_line !== null;
  const start = (f.lineno ?? 0) - (f.pre_context?.length ?? 0);
  return (
    <li className={f.in_app ? "frame app" : "frame"}>
      <div className="fhead" onClick={() => has && setOpen(!open)}>
        <code>{f.filename}</code> in <b>{f.function || "?"}</b> at line {f.lineno}{f.colno ? `:${f.colno}` : ""}
        {!f.in_app && <span className="muted"> (library)</span>}
      </div>
      {open && has && (
        <pre className="ctx">
          {f.pre_context?.map((l, i) => <div key={"p" + i}><span className="ln">{start + i}</span>{l}</div>)}
          <div className="hit"><span className="ln">{f.lineno}</span>{f.context_line}</div>
          {f.post_context?.map((l, i) => <div key={"a" + i}><span className="ln">{(f.lineno ?? 0) + 1 + i}</span>{l}</div>)}
        </pre>
      )}
    </li>
  );
}

export function IssueDetailPage({ id, back }: { id: number; back: string }) {
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [events, setEvents] = useState<TrutiEvent[]>([]);
  const [sel, setSel] = useState<TrutiEvent | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.issue(id).then((i) => { setIssue(i); setSel(i.latest_event); }).catch((e) => setErr(e.message));
    api.issueEvents(id).then(setEvents).catch(() => {});
  }, [id]);

  if (err) return <p className="err">{err}</p>;
  if (!issue) return <p className="muted">Loading…</p>;
  const ev = sel;
  const frames = ev?.exception?.frames ? [...ev.exception.frames].reverse() : []; // newest call first

  return (
    <section>
      <a href={back}>← Issues</a>
      <h2><span className={`lvl ${issue.level}`}>{issue.level}</span> {issue.title}</h2>
      <p className="muted">{issue.culprit} · {issue.count} events · first {timeAgo(issue.first_seen)} · last {timeAgo(issue.last_seen)}
        {issue.first_release && ` · first in ${issue.first_release}`}{issue.last_release && ` · last in ${issue.last_release}`} · <b>{issue.status}</b></p>
      <div className="row">
        {["unresolved", "resolved", "ignored"].filter((s) => s !== issue.status).map((s) => (
          <button key={s} onClick={async () => setIssue({ ...issue, ...(await api.setStatus(id, s)) })}>Mark {s}</button>
        ))}
      </div>

      {ev && (
        <>
          <div className="row">
            <label>Event <select value={ev.event_id} onChange={(e) => setSel(events.find((x) => x.event_id === e.target.value) ?? ev)}>
              {(events.length ? events : [ev]).map((x) => <option key={x.event_id} value={x.event_id}>{x.timestamp} · {x.release ?? "no release"}</option>)}
            </select></label>
          </div>
          {ev.exception && <>
            <h3>{ev.exception.type}: {ev.exception.value}</h3>
            <ul className="frames">{frames.map((f, i) => <FrameView key={i} f={f} />)}</ul>
          </>}
          {!ev.exception && <h3>{ev.message}</h3>}

          <div className="grid">
            <div className="card"><h4>Details</h4>
              <dl><dt>Release</dt><dd>{ev.release ?? "—"}</dd><dt>Environment</dt><dd>{ev.environment ?? "—"}</dd>
                <dt>Platform</dt><dd>{ev.platform}</dd><dt>Event ID</dt><dd><code>{ev.event_id}</code></dd>
                <dt>User</dt><dd>{ev.user ? <code>{JSON.stringify(ev.user)}</code> : "—"}</dd>
                {ev.request && <><dt>Request</dt><dd><code>{ev.request.method} {ev.request.url}</code></dd></>}</dl></div>
            <div className="card"><h4>Tags</h4>
              {Object.keys(ev.tags ?? {}).length === 0 ? <span className="muted">none</span> :
                Object.entries(ev.tags!).map(([k, v]) => <span className="tag" key={k}>{k}: <b>{v}</b></span>)}</div>
          </div>

          <h4>Breadcrumbs</h4>
          {(ev.breadcrumbs ?? []).length === 0 ? <p className="muted">none</p> : (
            <table><tbody>{ev.breadcrumbs!.map((b, i) => (
              <tr key={i}><td className="muted">{b.timestamp?.slice(11, 23)}</td><td>{b.category}</td><td>{b.message}</td></tr>))}</tbody></table>
          )}
        </>
      )}
    </section>
  );
}

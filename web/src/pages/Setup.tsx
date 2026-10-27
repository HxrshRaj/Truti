import { useState } from "react";
import { api, Project } from "../api";

export function Setup({ projects, onChanged }: { projects: Project[]; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  return (
    <section>
      <h2>Projects &amp; DSN</h2>
      <form className="row" onSubmit={async (e) => {
        e.preventDefault();
        try { const p = await api.createProject(name); setName(""); await onChanged(); location.hash = `#/projects/${p.id}/setup`; }
        catch (x) { setErr((x as Error).message); }
      }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New project name" required />
        <button>Create project</button>
      </form>
      {err && <p className="err">{err}</p>}
      {projects.map((p) => (
        <div className="card" key={p.id}>
          <b>{p.name}</b> <span className="muted">#{p.id}</span>
          <div className="dsn"><code>{p.dsn}</code>
            <button className="link" onClick={() => navigator.clipboard?.writeText(p.dsn)}>copy</button></div>
          <pre>{`# Python\nimport truti\ntruti.init(dsn="${p.dsn}", release="1.0.0")\n\n// JavaScript\nimport { init } from "truti-js";\ninit({ dsn: "${p.dsn}", release: "1.0.0" });`}</pre>
        </div>
      ))}
    </section>
  );
}

import { useCallback, useEffect, useState } from "react";
import { Alerts } from "./pages/Alerts";
import { IssueDetailPage } from "./pages/IssueDetail";
import { IssuesPage } from "./pages/Issues";
import { Login } from "./pages/Login";
import { Setup } from "./pages/Setup";
import { api, ApiError, config, Project } from "./api";
import { parseRoute } from "./lib";

export function App() {
  const [hash, setHash] = useState(location.hash);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [authError, setAuthError] = useState<string | null>(config.token ? null : "");

  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const load = useCallback(async () => {
    try {
      setProjects(await api.projects());
      setAuthError(null);
    } catch (e) {
      setProjects(null);
      setAuthError(e instanceof ApiError && e.status === 401 ? "Session expired or invalid; sign in again." : `Cannot reach API: ${(e as Error).message}`);
    }
  }, []);
  useEffect(() => { if (config.token) void load(); }, [load]);

  if (authError !== null || !projects) return <Login error={authError} onDone={load} />;

  const route = parseRoute(hash);
  const project = projects.find((p) => p.id === route.project) ?? (route.project ? undefined : projects[0]);
  const base = project ? `#/projects/${project.id}` : "#/";

  return (
    <div className="shell">
      <header>
        <a className="brand" href="#/">Truti <small>त्रुटि</small></a>
        <select value={project?.id ?? ""} onChange={(e) => (location.hash = `#/projects/${e.target.value}/issues`)}>
          {projects.length === 0 && <option value="">No projects</option>}
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <nav>
          <a className={route.tab === "issues" ? "on" : ""} href={`${base}/issues`}>Issues</a>
          <a className={route.tab === "alerts" ? "on" : ""} href={`${base}/alerts`}>Alerts</a>
          <a className={route.tab === "setup" ? "on" : ""} href={`${base}/setup`}>Projects &amp; DSN</a>
        </nav>
        <button className="link" onClick={() => { void fetch(`${config.apiUrl}/api/0/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${config.token}` } }).catch(() => {}); config.clear(); setProjects(null); setAuthError(""); }}>Sign out</button>
      </header>
      <main>
        {route.tab === "setup" || !project ? (
          <Setup projects={projects} onChanged={load} />
        ) : route.tab === "alerts" ? (
          <Alerts project={project} />
        ) : route.issue ? (
          <IssueDetailPage id={route.issue} back={`${base}/issues`} />
        ) : (
          <IssuesPage project={project} />
        )}
      </main>
    </div>
  );
}

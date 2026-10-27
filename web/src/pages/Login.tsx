import { useState } from "react";
import { config } from "../api";

export function Login({ error, onDone }: { error: string | null; onDone: () => void }) {
  const [url, setUrl] = useState(config.apiUrl);
  const [token, setToken] = useState("");
  return (
    <div className="login">
      <h1>Truti <small>त्रुटि</small></h1>
      <p className="muted">Connect to your ingest/API service.</p>
      <form onSubmit={(e) => { e.preventDefault(); config.save(url, token); onDone(); }}>
        <label>API URL <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="(blank = same origin)" /></label>
        <label>Admin token <input type="password" value={token} onChange={(e) => setToken(e.target.value)} required /></label>
        {error && <p className="err">{error}</p>}
        <button type="submit">Connect</button>
      </form>
    </div>
  );
}

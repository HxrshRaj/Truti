import { useState } from "react";
import { authenticate, config } from "../api";

type Mode = "login" | "register" | "token";

export function Login({ error, onDone }: { error: string | null; onDone: () => void }) {
  const [url, setUrl] = useState(config.apiUrl);
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const tok = mode === "token" ? token : await authenticate(url, mode, email, password);
      config.save(url, tok);
      onDone();
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <h1>Truti <small>त्रुटि</small></h1>
      <p className="muted">
        {mode === "register" ? "Create an account. The first account on a new server becomes its admin." :
         mode === "token" ? "Use the server's bootstrap ADMIN_TOKEN." : "Sign in to your Truti server."}
      </p>
      <form onSubmit={submit}>
        <label>API URL <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="(blank = same origin)" /></label>
        {mode === "token" ? (
          <label>Admin token <input type="password" value={token} onChange={(e) => setToken(e.target.value)} required /></label>
        ) : (<>
          <label>Email <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label>Password <input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8}
            value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        </>)}
        {(err ?? error) && <p className="err">{err ?? error}</p>}
        <button type="submit" disabled={busy}>{mode === "login" ? "Sign in" : mode === "register" ? "Create account" : "Connect"}</button>
      </form>
      <p className="muted">
        {mode !== "login" && <button className="link" onClick={() => setMode("login")}>Sign in</button>}{" "}
        {mode !== "register" && <button className="link" onClick={() => setMode("register")}>Create account</button>}{" "}
        {mode !== "token" && <button className="link" onClick={() => setMode("token")}>Use admin token</button>}
      </p>
    </div>
  );
}

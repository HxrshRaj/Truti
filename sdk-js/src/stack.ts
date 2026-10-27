export interface Frame {
  filename: string;
  function?: string;
  lineno?: number;
  colno?: number;
  context_line?: string;
  pre_context?: string[];
  post_context?: string[];
  in_app: boolean;
}

// V8:      "    at fn (file:1:2)"  /  "    at file:1:2"  /  "    at async fn (file:1:2)"
const V8_RE = /^\s*at (?:(.*?) \()?(.+?):(\d+):(\d+)\)?\s*$/;
// Firefox/Safari: "fn@file:1:2"  /  "@file:1:2"
const GECKO_RE = /^(.*?)@(.+?):(\d+):(\d+)\s*$/;

function normaliseFile(f: string): string {
  return f.replace(/^file:\/\/\/?(?=[A-Za-z]:|\/)/, "").replace(/^file:\/\//, "");
}

/** Parse an Error.stack string into frames, oldest call first (newest last). */
export function parseStack(stack: string | undefined): Frame[] {
  if (!stack) return [];
  const frames: Frame[] = [];
  for (const line of stack.split("\n")) {
    const m = V8_RE.exec(line) ?? GECKO_RE.exec(line);
    if (!m) continue;
    let fn = (m[1] || "").replace(/^async /, "").trim();
    if (V8_RE.test(line) === false && fn === "") fn = "";
    const filename = normaliseFile(m[2]);
    if (filename === "<anonymous>" || filename.startsWith("native")) continue;
    frames.push({
      filename,
      function: fn || "<anonymous>",
      lineno: Number(m[3]),
      colno: Number(m[4]),
      in_app: !filename.includes("node_modules") && !filename.startsWith("node:"),
    });
  }
  return frames.reverse().slice(-100);
}

/** Node only: attach source lines for frames pointing at readable local files. */
export function addSourceContext(frames: Frame[], read: (file: string) => string | undefined, n = 5): void {
  const cache = new Map<string, string[] | undefined>();
  for (const f of frames) {
    if (!f.in_app || !f.lineno || f.filename.startsWith("http") || f.filename.startsWith("node:")) continue;
    if (!cache.has(f.filename)) {
      let text: string | undefined;
      try { text = read(f.filename); } catch { text = undefined; }
      cache.set(f.filename, text?.split(/\r?\n/));
    }
    const lines = cache.get(f.filename);
    if (!lines || f.lineno > lines.length) continue;
    f.context_line = lines[f.lineno - 1];
    f.pre_context = lines.slice(Math.max(0, f.lineno - 1 - n), f.lineno - 1);
    f.post_context = lines.slice(f.lineno, f.lineno + n);
  }
}

import { scrub } from "./scrub";
import { addSourceContext, parseStack } from "./stack";
import { Transport, TransportOptions } from "./transport";

export const VERSION = "0.1.0";
export { parseStack, scrub, Transport };

export interface InitOptions extends Omit<TransportOptions, "dsn"> {
  dsn: string;
  release?: string;
  environment?: string;
  sampleRate?: number;
  beforeSend?: (event: any) => any | null;
  autoHooks?: boolean;
}

export interface Hint {
  level?: "fatal" | "error" | "warning" | "info" | "debug";
  tags?: Record<string, string>;
  user?: Record<string, unknown>;
  request?: Record<string, unknown>;
}

const MAX_BREADCRUMBS = 100;
const scope = { tags: {} as Record<string, string>, user: null as Record<string, unknown> | null, breadcrumbs: [] as any[] };

let transport: Transport | null = null;
let opts: InitOptions | null = null;
let removeHooks: (() => void) | null = null;

function hex32(): string {
  const c: any = (globalThis as any).crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

function readFile(file: string): string | undefined {
  const p: any = (globalThis as any).process;
  const fs = p?.getBuiltinModule?.("node:fs");
  return fs ? fs.readFileSync(file, "utf8") : undefined;
}

function send(event: any): string | undefined {
  try {
    if (!transport || !opts) return undefined;
    if (opts.sampleRate !== undefined && opts.sampleRate < 1 && Math.random() >= opts.sampleRate) return undefined;
    event = scrub(event);
    if (opts.beforeSend) {
      event = opts.beforeSend(event);
      if (!event) return undefined;
    }
    return transport.enqueue(event) ? event.event_id : undefined;
  } catch {
    return undefined;
  }
}

function baseEvent(level: string, hint?: Hint) {
  return {
    event_id: hex32(),
    timestamp: new Date().toISOString(),
    platform: "javascript",
    level: hint?.level ?? level,
    release: opts?.release,
    environment: opts?.environment,
    tags: { ...scope.tags, ...(hint?.tags ?? {}) },
    user: hint?.user ?? scope.user ?? undefined,
    request: hint?.request,
    breadcrumbs: [...scope.breadcrumbs],
    sdk: { name: "truti.javascript", version: VERSION },
  };
}

export function init(options: InitOptions): boolean {
  try {
    close();
    transport = new Transport({ ...options, sdkVersion: VERSION });
    opts = options;
    if (options.autoHooks !== false) removeHooks = installHooks();
    return true;
  } catch {
    transport = null;
    opts = null;
    return false; // invalid DSN etc. disables the SDK instead of throwing
  }
}

export function captureException(err: unknown, hint?: Hint): string | undefined {
  try {
    if (!transport) return undefined;
    const e: any = err;
    const isErr = e && typeof e === "object" && typeof e.message === "string";
    const frames = parseStack(isErr ? e.stack : new Error().stack);
    addSourceContext(frames, readFile);
    const ev: any = baseEvent("error", hint);
    ev.exception = {
      type: isErr ? String(e.name || "Error") : "NonErrorException",
      value: isErr ? e.message : safeString(err),
      frames,
    };
    ev.message = `${ev.exception.type}: ${ev.exception.value}`;
    return send(ev);
  } catch {
    return undefined;
  }
}

export function captureMessage(message: string, level: Hint["level"] = "info", hint?: Hint): string | undefined {
  try {
    if (!transport) return undefined;
    return send({ ...baseEvent(level ?? "info", hint), message });
  } catch {
    return undefined;
  }
}

export function setUser(user: Record<string, unknown> | null): void { scope.user = user; }
export function setTag(key: string, value: string): void { scope.tags[key] = String(value); }
export function addBreadcrumb(b: { message: string; category?: string; level?: string; data?: unknown }): void {
  scope.breadcrumbs.push({ timestamp: new Date().toISOString(), category: "default", level: "info", ...b });
  if (scope.breadcrumbs.length > MAX_BREADCRUMBS) scope.breadcrumbs.shift();
}

export function flush(timeoutMs = 2000): Promise<boolean> {
  return transport ? transport.flush(timeoutMs).catch(() => false) : Promise.resolve(true);
}

export function close(): void {
  removeHooks?.();
  removeHooks = null;
  transport = null;
  opts = null;
}

export function getTransport(): Transport | null { return transport; }

export function resetScope(): void {
  scope.tags = {};
  scope.user = null;
  scope.breadcrumbs = [];
}

function safeString(v: unknown): string {
  try { return typeof v === "string" ? v : JSON.stringify(v) ?? String(v); } catch { return String(v); }
}

function installHooks(): () => void {
  const g: any = globalThis;
  const undo: Array<() => void> = [];

  if (typeof g.window !== "undefined" && g.window.addEventListener) {
    const onError = (ev: any) => { captureException(ev.error ?? new Error(String(ev.message)), { level: "error" }); };
    const onRej = (ev: any) => { captureException(ev.reason ?? new Error("unhandledrejection"), { level: "error" }); };
    g.window.addEventListener("error", onError);
    g.window.addEventListener("unhandledrejection", onRej);
    undo.push(() => {
      g.window.removeEventListener("error", onError);
      g.window.removeEventListener("unhandledrejection", onRej);
    });
  } else if (g.process?.on) {
    const p = g.process;
    const onUncaught = (err: unknown) => {
      captureException(err, { level: "fatal" });
      // We replaced Node's default crash behaviour; restore it unless someone else handles the error.
      void flush(2000).then(() => { if (p.listenerCount("uncaughtException") <= 1) p.exit(1); });
    };
    const onRej = (reason: unknown) => { captureException(reason, { level: "error" }); };
    const onBeforeExit = () => { void flush(2000); };
    p.on("uncaughtException", onUncaught);
    p.on("unhandledRejection", onRej);
    p.on("beforeExit", onBeforeExit);
    undo.push(() => {
      p.off("uncaughtException", onUncaught);
      p.off("unhandledRejection", onRej);
      p.off("beforeExit", onBeforeExit);
    });
  }
  return () => undo.forEach((f) => f());
}

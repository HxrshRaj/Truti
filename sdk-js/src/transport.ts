export interface TransportOptions {
  dsn: string;
  maxQueue?: number;
  batchSize?: number;
  flushIntervalMs?: number;
  maxRetries?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  timeoutMs?: number;
  fetch?: typeof fetch;
  sdkVersion?: string;
}

export function parseDsn(dsn: string): { url: string; key: string } {
  let u: URL;
  try { u = new URL(dsn); } catch { throw new Error("invalid DSN"); }
  const parts = u.pathname.split("/").filter(Boolean);
  if (!u.username || !parts.length) throw new Error("invalid DSN: expected scheme://publickey@host/project_id");
  const projectId = parts[parts.length - 1];
  const prefix = parts.slice(0, -1).join("/");
  return { url: `${u.protocol}//${u.host}${prefix ? "/" + prefix : ""}/api/${projectId}/envelope/`, key: u.username };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Bounded queue + background batching sender. Nothing here rejects into the host app. */
export class Transport {
  readonly stats = { sent: 0, droppedQueueFull: 0, droppedFailed: 0, retries: 0 };
  private queue: any[] = [];
  private pending = 0;
  private running = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private waiters: Array<() => void> = [];
  private url: string;
  private key: string;
  private o: Required<Omit<TransportOptions, "dsn" | "fetch" | "sdkVersion">>;
  private fetchFn: typeof fetch;
  private sdkVersion: string;

  constructor(opts: TransportOptions) {
    ({ url: this.url, key: this.key } = parseDsn(opts.dsn));
    this.o = {
      maxQueue: opts.maxQueue ?? 100,
      batchSize: opts.batchSize ?? 20,
      flushIntervalMs: opts.flushIntervalMs ?? 500,
      maxRetries: opts.maxRetries ?? 5,
      baseDelayMs: opts.baseDelayMs ?? 500,
      maxDelayMs: opts.maxDelayMs ?? 30_000,
      timeoutMs: opts.timeoutMs ?? 5_000,
    };
    this.fetchFn = opts.fetch ?? ((...a) => fetch(...a));
    this.sdkVersion = opts.sdkVersion ?? "0.1.0";
  }

  enqueue(event: unknown): boolean {
    if (this.queue.length >= this.o.maxQueue) {
      this.stats.droppedQueueFull++;
      return false;
    }
    this.queue.push(event);
    this.pending++;
    if (!this.running && this.timer === undefined) {
      this.timer = setTimeout(() => { this.timer = undefined; void this.run(); }, this.o.flushIntervalMs);
      (this.timer as any)?.unref?.();
    }
    return true;
  }

  /** Resolves true when everything queued was delivered or dropped, false on timeout. */
  flush(timeoutMs = 2000): Promise<boolean> {
    if (this.pending === 0) return Promise.resolve(true);
    if (this.timer !== undefined) { clearTimeout(this.timer); this.timer = undefined; }
    void this.run();
    return new Promise((resolve) => {
      const t = setTimeout(() => resolve(false), timeoutMs);
      (t as any)?.unref?.();
      this.waiters.push(() => { clearTimeout(t); resolve(true); });
    });
  }

  private async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      while (this.queue.length) {
        const batch = this.queue.splice(0, this.o.batchSize);
        let ok = false;
        try { ok = await this.deliver(batch); } catch { ok = false; }
        this.stats[ok ? "sent" : "droppedFailed"] += batch.length;
        this.pending -= batch.length;
        if (this.pending === 0) { const w = this.waiters; this.waiters = []; w.forEach((f) => f()); }
      }
    } finally {
      this.running = false;
    }
  }

  private async deliver(batch: unknown[]): Promise<boolean> {
    const body = JSON.stringify({ envelope_version: 1, sent_at: new Date().toISOString(), events: batch });
    for (let attempt = 0; attempt <= this.o.maxRetries; attempt++) {
      const { status, retryAfterMs } = await this.post(body);
      if (status !== null && status >= 200 && status < 300) return true;
      if (status !== null && status !== 429 && status < 500) return false; // client error: retrying won't help
      if (attempt === this.o.maxRetries) break;
      this.stats.retries++;
      const delay = retryAfterMs !== null
        ? Math.min(retryAfterMs, this.o.maxDelayMs)
        : Math.random() * Math.min(this.o.maxDelayMs, this.o.baseDelayMs * 2 ** attempt); // full jitter
      await sleep(delay);
    }
    return false;
  }

  private async post(body: string): Promise<{ status: number | null; retryAfterMs: number | null }> {
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : undefined;
    const t = setTimeout(() => ctl?.abort(), this.o.timeoutMs);
    try {
      const res = await this.fetchFn(this.url, {
        method: "POST",
        body,
        headers: { "Content-Type": "application/json", "X-Truti-Auth": `truti_key=${this.key}` },
        keepalive: body.length < 60_000,
        signal: ctl?.signal,
      });
      const ra = Number(res.headers?.get?.("Retry-After"));
      return { status: res.status, retryAfterMs: Number.isFinite(ra) && res.headers?.get?.("Retry-After") ? ra * 1000 : null };
    } catch {
      return { status: null, retryAfterMs: null };
    } finally {
      clearTimeout(t);
    }
  }
}

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import * as truti from "../src/index";
import { errorHandler, requestHandler } from "../src/express";
import { parseStack } from "../src/stack";
import { parseDsn } from "../src/transport";
import { scrub } from "../src/scrub";

type Call = { url: string; headers: any; body: any };
function fakeFetch(script: Array<number | { status: number; retryAfter?: string } | "throw" | "hang"> = []) {
  const calls: Call[] = [];
  const f = (async (url: string, init: any) => {
    const step = script.shift() ?? 202;
    if (step === "throw") throw new TypeError("fetch failed");
    if (step === "hang") return new Promise((_r, rej) => init.signal?.addEventListener("abort", () => rej(new Error("aborted"))));
    const s = typeof step === "number" ? { status: step } : step;
    if (s.status < 300) calls.push({ url, headers: init.headers, body: JSON.parse(init.body) });
    return { status: s.status, headers: { get: (k: string) => (k === "Retry-After" ? (s as any).retryAfter ?? null : null) } };
  }) as any;
  return { f, calls, events: () => calls.flatMap((c) => c.body.events) };
}

const DSN = "http://pubkey@localhost:9/5";
const init = (fetch: any, extra: any = {}) =>
  truti.init({ dsn: DSN, fetch, autoHooks: false, flushIntervalMs: 10, baseDelayMs: 5, ...extra });

beforeEach(() => truti.resetScope());
afterEach(() => truti.close());

describe("capture", () => {
  it("sends an envelope with frames, tags, user, breadcrumbs, release", async () => {
    const { f, calls, events } = fakeFetch();
    init(f, { release: "1.0.0", environment: "test" });
    truti.setTag("region", "eu");
    truti.setUser({ id: "u1" });
    truti.addBreadcrumb({ message: "clicked", category: "ui" });
    function boom() { throw new RangeError("bad range"); }
    try { boom(); } catch (e) { truti.captureException(e); }
    expect(await truti.flush(3000)).toBe(true);
    const ev = events()[0];
    expect(calls[0].url).toBe("http://localhost:9/api/5/envelope/");
    expect(calls[0].headers["X-Truti-Auth"]).toBe("truti_key=pubkey");
    expect(calls[0].body.envelope_version).toBe(1);
    expect(ev.exception.type).toBe("RangeError");
    expect(ev.exception.value).toBe("bad range");
    expect(ev.exception.frames.length).toBeGreaterThan(0);
    const last = ev.exception.frames.at(-1);
    expect(last.function).toContain("boom");
    expect(last.context_line).toContain("RangeError"); // Node source context
    expect(ev).toMatchObject({ platform: "javascript", release: "1.0.0", environment: "test", tags: { region: "eu" }, user: { id: "u1" } });
    expect(ev.breadcrumbs[0].message).toBe("clicked");
    expect(ev.event_id).toMatch(/^[0-9a-f]{32}$/);
  });

  it("captures non-Error throwables and messages", async () => {
    const { f, events } = fakeFetch();
    init(f);
    truti.captureException({ code: 7 });
    truti.captureMessage("hi", "warning");
    await truti.flush(3000);
    expect(events()[0].exception.type).toBe("NonErrorException");
    expect(events()[1]).toMatchObject({ message: "hi", level: "warning" });
  });

  it("scrubs secrets", async () => {
    const { f, events } = fakeFetch();
    init(f);
    truti.addBreadcrumb({ message: "login", data: { password: "hunter2" } });
    truti.captureMessage("failed token=abc123");
    await truti.flush(3000);
    expect(JSON.stringify(events())).not.toMatch(/hunter2|abc123/);
    expect(scrub({ Authorization: "x" }).Authorization).toBe("[Filtered]");
  });

  it("beforeSend can drop/modify; sampleRate 0 sends nothing", async () => {
    const a = fakeFetch();
    init(a.f, { beforeSend: (e: any) => (e.message === "drop" ? null : { ...e, tags: { t: "1" } }) });
    truti.captureMessage("drop");
    truti.captureMessage("keep");
    await truti.flush(3000);
    expect(a.events().map((e: any) => e.message)).toEqual(["keep"]);
    expect(a.events()[0].tags).toEqual({ t: "1" });
    const b = fakeFetch();
    init(b.f, { sampleRate: 0 });
    truti.captureMessage("x");
    await truti.flush(100);
    expect(b.events()).toEqual([]);
  });

  it("invalid DSN disables the SDK without throwing", async () => {
    expect(truti.init({ dsn: "nonsense" })).toBe(false);
    expect(truti.captureMessage("x")).toBeUndefined();
    expect(await truti.flush()).toBe(true);
    expect(() => parseDsn("http://h/1")).toThrow();
  });

  it("a throwing beforeSend never propagates", () => {
    init(fakeFetch().f, { beforeSend: () => { throw new Error("x"); } });
    expect(() => truti.captureMessage("x")).not.toThrow();
  });
});

describe("transport", () => {
  it("retries 5xx and network errors, then succeeds", async () => {
    const { f, events } = fakeFetch([500, "throw", 503]);
    init(f);
    truti.captureMessage("x");
    expect(await truti.flush(5000)).toBe(true);
    expect(events().length).toBe(1);
    expect(truti.getTransport()!.stats.retries).toBe(3);
  });

  it("honours Retry-After on 429", async () => {
    const { f, events } = fakeFetch([{ status: 429, retryAfter: "1" }]);
    init(f);
    const t0 = Date.now();
    truti.captureMessage("x");
    expect(await truti.flush(5000)).toBe(true);
    expect(Date.now() - t0).toBeGreaterThanOrEqual(950);
    expect(events().length).toBe(1);
  });

  it("drops on 4xx without retrying", async () => {
    const { f } = fakeFetch([400]);
    init(f);
    truti.captureMessage("x");
    await truti.flush(2000);
    const s = truti.getTransport()!.stats;
    expect([s.retries, s.droppedFailed]).toEqual([0, 1]);
  });

  it("gives up after maxRetries when server is down", async () => {
    const { f } = fakeFetch(["throw", "throw", "throw"]);
    init(f, { maxRetries: 2 });
    truti.captureMessage("x");
    expect(await truti.flush(5000)).toBe(true);
    expect(truti.getTransport()!.stats.droppedFailed).toBe(1);
  });

  it("slow server: caller never blocks, flush times out then request aborts", async () => {
    const { f } = fakeFetch(["hang"]);
    init(f, { timeoutMs: 100, maxRetries: 0 });
    const t0 = Date.now();
    truti.captureMessage("x");
    expect(Date.now() - t0).toBeLessThan(50);
    expect(await truti.flush(20)).toBe(false);
    expect(await truti.flush(2000)).toBe(true);
    expect(truti.getTransport()!.stats.droppedFailed).toBe(1);
  });

  it("bounded queue drops when full", () => {
    init(fakeFetch(["hang"]).f, { maxQueue: 3, timeoutMs: 50, maxRetries: 0 });
    const r = Array.from({ length: 10 }, () => truti.captureMessage("x"));
    expect(r.filter((x) => x === undefined).length).toBe(7);
    expect(truti.getTransport()!.stats.droppedQueueFull).toBe(7);
  });

  it("batches events into one request", async () => {
    const { f, calls } = fakeFetch();
    init(f, { flushIntervalMs: 100 });
    for (let i = 0; i < 5; i++) truti.captureMessage("m" + i);
    await truti.flush(3000);
    expect(calls.length).toBe(1);
    expect(calls[0].body.events.length).toBe(5);
  });
});

describe("stack parsing", () => {
  it("parses V8 frames oldest-first and marks node_modules as not in_app", () => {
    const frames = parseStack(
      "Error: x\n    at inner (/app/src/a.js:10:5)\n    at async outer (file:///app/src/b.js:3:1)\n    at /app/node_modules/lib/i.js:1:1\n    at new Foo (http://x.com/app.min.js:1:2345)",
    );
    expect(frames.map((f) => f.function)).toEqual(["Foo", "<anonymous>", "outer", "inner"].map((x) => (x === "Foo" ? "new Foo" : x)));
    expect(frames[3]).toMatchObject({ filename: "/app/src/a.js", lineno: 10, colno: 5, in_app: true });
    expect(frames[2].filename).toBe("/app/src/b.js");
    expect(frames[1].in_app).toBe(false);
    expect(frames[0]).toMatchObject({ filename: "http://x.com/app.min.js", lineno: 1, colno: 2345 });
  });

  it("parses Firefox/Safari frames", () => {
    const frames = parseStack("inner@http://x.com/a.js:10:5\n@http://x.com/b.js:3:1");
    expect(frames.map((f) => [f.function, f.lineno])).toEqual([["<anonymous>", 3], ["inner", 10]]);
  });
});

describe("hooks and express", () => {
  it("window error + unhandledrejection are captured", async () => {
    const handlers: Record<string, (e: any) => void> = {};
    (globalThis as any).window = {
      addEventListener: (n: string, h: any) => (handlers[n] = h),
      removeEventListener: (n: string) => delete handlers[n],
    };
    try {
      const { f, events } = fakeFetch();
      init(f, { autoHooks: true });
      handlers["error"]({ error: new TypeError("oops"), message: "oops" });
      handlers["unhandledrejection"]({ reason: new Error("rejected") });
      await truti.flush(3000);
      expect(events().map((e: any) => e.exception.type)).toEqual(["TypeError", "Error"]);
      truti.close();
      expect(Object.keys(handlers)).toEqual([]);
    } finally {
      delete (globalThis as any).window;
    }
  });

  it("express middleware captures with request info and forwards the error", async () => {
    const { f, events } = fakeFetch();
    init(f);
    const next = (e?: unknown) => { forwarded = e; };
    let forwarded: unknown;
    requestHandler()({ method: "GET", url: "/x" }, {}, () => {});
    const err = new Error("route failed");
    errorHandler()(err, { method: "GET", originalUrl: "/boom", headers: { authorization: "Bearer abcdefghijk", host: "h" } }, {}, next);
    await truti.flush(3000);
    expect(forwarded).toBe(err);
    const ev = events()[0];
    expect(ev.request.url).toBe("/boom");
    expect(ev.request.headers.authorization).toBe("[Filtered]");
    expect(ev.breadcrumbs.some((b: any) => b.category === "http")).toBe(true);
  });
});

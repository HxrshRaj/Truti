import { addBreadcrumb, captureException } from "./index";

const SENSITIVE = new Set(["authorization", "cookie", "set-cookie", "x-api-key"]);

/** Request middleware: adds a breadcrumb per request. Mount first. */
export function requestHandler() {
  return (req: any, _res: any, next: () => void) => {
    try { addBreadcrumb({ message: `${req.method} ${req.originalUrl ?? req.url}`, category: "http" }); } catch { /* never throw */ }
    next();
  };
}

/** Error middleware: captures and passes the error on. Mount after your routes. */
export function errorHandler() {
  return (err: unknown, req: any, _res: any, next: (e?: unknown) => void) => {
    try {
      const headers: Record<string, string> = {};
      for (const [k, v] of Object.entries(req.headers ?? {})) headers[k] = SENSITIVE.has(k.toLowerCase()) ? "[Filtered]" : String(v);
      captureException(err, { request: { method: req.method, url: req.originalUrl ?? req.url, headers } });
    } catch { /* never throw */ }
    next(err);
  };
}

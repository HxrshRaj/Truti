export const FILTERED = "[Filtered]";
const KEY_RE = /pass(word|wd)?|secret|token|api[-_]?key|authorization|auth|cookie|session|credit|card|cvv|ssn|private[-_]?key/i;
const BEARER_RE = /\bbearer\s+[a-z0-9._~+/=-]{8,}/gi;
const KV_RE = /\b(password|passwd|token|secret|api[-_]?key)=([^&\s]+)/gi;
const MAX_DEPTH = 8;

export function scrubString(s: string): string {
  return s.replace(BEARER_RE, "Bearer " + FILTERED).replace(KV_RE, (_m, k) => `${k}=${FILTERED}`);
}

/** Returns a copy of `value` with sensitive keys masked and obvious secrets removed from strings. */
export function scrub(value: unknown, depth = 0): any {
  if (depth > MAX_DEPTH) return FILTERED;
  if (typeof value === "string") return scrubString(value);
  if (Array.isArray(value)) return value.map((v) => scrub(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = KEY_RE.test(k) ? FILTERED : scrub(v, depth + 1);
    }
    return out;
  }
  return value;
}

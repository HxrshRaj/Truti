/** SVG polyline points for a sparkline in a w×h box. */
export function sparkPoints(data: number[], w = 96, h = 24): string {
  if (data.length === 0) return "";
  const max = Math.max(...data, 1);
  const step = data.length > 1 ? w / (data.length - 1) : 0;
  return data.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - (v / max) * (h - 4)).toFixed(1)}`).join(" ");
}

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Hash route: "#/projects/3/issues/12" -> { project: 3, issue: 12, tab: "issues" } */
export function parseRoute(hash: string): { project?: number; issue?: number; tab: "issues" | "alerts" | "setup" } {
  const m = /^#\/projects\/(\d+)(?:\/(issues|alerts|setup))?(?:\/(\d+))?/.exec(hash);
  if (!m) return { tab: "issues" };
  return { project: Number(m[1]), tab: (m[2] as any) ?? "issues", issue: m[3] ? Number(m[3]) : undefined };
}

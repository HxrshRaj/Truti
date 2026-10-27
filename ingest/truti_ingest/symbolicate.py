"""Source map symbolication (own implementation of source map v3 decoding).

Algorithm
---------
1. A map's ``mappings`` string is ';'-separated generated lines; each line is ','-separated
   segments; each segment is 1, 4 or 5 base64-VLQ numbers:
   [generatedColumn, sourceIndex, originalLine, originalColumn, nameIndex].
2. All fields are *deltas*: generatedColumn resets to 0 on every new line, the rest carry
   across the whole file. We decode into absolute values and keep, per generated line, a list
   of segments sorted by generatedColumn.
3. To resolve a minified frame (line L, column C; both 1-based in the event, 0-based in the
   map) we take generated line L-1 and binary-search for the last segment with
   generatedColumn <= C-1 (the "greatest lower bound"). That segment names the original
   source, line, column and optionally the symbol name.
4. If the map has ``sourcesContent`` we also rebuild context_line/pre/post from the original.
"""
import bisect
import json

_B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def _vlq(segment: str):
    out, shift, value = [], 0, 0
    for ch in segment:
        d = _B64[ch]
        value |= (d & 31) << shift
        if d & 32:
            shift += 5
        else:
            out.append(-(value >> 1) if value & 1 else value >> 1)
            shift = value = 0
    return out


class SourceMapIndex:
    def __init__(self, raw: str):
        m = json.loads(raw)
        if m.get("version") != 3:
            raise ValueError("unsupported source map version")
        root = m.get("sourceRoot") or ""
        self.sources = [(root + s) for s in m["sources"]]
        self.contents = m.get("sourcesContent") or [None] * len(self.sources)
        self.names = m.get("names", [])
        self.lines = []  # per generated line: (cols[], segs[])
        src = oline = ocol = name = 0
        for line in m["mappings"].split(";"):
            col = 0
            cols, segs = [], []
            for seg in filter(None, line.split(",")):
                f = _vlq(seg)
                col += f[0]
                if len(f) >= 4:
                    src += f[1]; oline += f[2]; ocol += f[3]
                    if len(f) == 5:
                        name += f[4]
                    segs.append((src, oline, ocol, name if len(f) == 5 else None))
                else:
                    segs.append(None)  # segment with no source
                cols.append(col)
            self.lines.append((cols, segs))

    def lookup(self, line: int, col: int):
        """1-based line, 0-or-1-based col (we treat as 0-based like browsers' colno-1)."""
        if line < 1 or line > len(self.lines):
            return None
        cols, segs = self.lines[line - 1]
        i = bisect.bisect_right(cols, col) - 1
        if i < 0 or segs[i] is None:
            return None
        s, ol, oc, n = segs[i]
        return {"source": self.sources[s], "line": ol + 1, "col": oc,
                "name": self.names[n] if n is not None and n < len(self.names) else None,
                "content": self.contents[s] if s < len(self.contents) else None}


def symbolicate_frames(frames: list[dict], find_map, context=5):
    """``find_map(filename) -> SourceMapIndex | None``. Returns (new_frames, resolved_count)."""
    out, resolved = [], 0
    for f in frames:
        f = dict(f)
        idx = find_map(f["filename"]) if f.get("lineno") and f.get("colno") is not None else None
        hit = idx.lookup(f["lineno"], max(f["colno"] - 1, 0)) if idx else None
        if hit:
            resolved += 1
            f.update({"abs_path": f["filename"], "filename": hit["source"], "lineno": hit["line"],
                      "colno": hit["col"] + 1, "symbolicated": True})
            if hit["name"]:
                f["function"] = hit["name"]
            if hit["content"]:
                lines = hit["content"].split("\n")
                n = hit["line"]
                f["context_line"] = lines[n - 1] if 0 < n <= len(lines) else None
                f["pre_context"] = lines[max(0, n - 1 - context): n - 1]
                f["post_context"] = lines[n: n + context]
            else:
                f["context_line"], f["pre_context"], f["post_context"] = None, [], []
            f["in_app"] = "node_modules" not in hit["source"]
        out.append(f)
    return out, resolved

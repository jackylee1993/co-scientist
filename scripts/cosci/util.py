"""Small, dependency-free helpers shared by the Co-Scientist skill."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Encoding / IO
# ---------------------------------------------------------------------------

def force_utf8_stdout() -> None:
    """Windows consoles default to GBK; make JSON/Chinese output safe."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def read_json(path: str | Path) -> Any:
    p = Path(path)
    with p.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def read_json_loose(path: str | Path) -> Any:
    """Read JSON that may be wrapped in a markdown fence or surrounded by prose.

    Subagent outputs are untrusted; this only recovers the outermost JSON
    value, it does not evaluate anything.
    """
    p = Path(path)
    try:
        return read_json(p)
    except json.JSONDecodeError:
        text = read_text(p).strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(text.rfind("]"), text.rfind("}"))
        if end <= start:
            raise
        return json.loads(text[start:end + 1])


def write_json(path: str | Path, data: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return p


def read_text(path: str | Path, default: str = "") -> str:
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding="utf-8-sig", errors="replace")


def write_text(path: str | Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def append_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def safe_slug(text: str, max_len: int = 48) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-") or "run")


# ---------------------------------------------------------------------------
# Text / similarity
# ---------------------------------------------------------------------------

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "by", "is", "are", "was", "were", "be", "been", "this", "that", "these",
    "those", "we", "our", "it", "its", "as", "at", "from", "into", "via",
    "can", "could", "may", "might", "will", "would", "should", "not", "no",
    "than", "then", "using", "use", "used", "study", "research", "results",
    "基于", "以及", "通过", "进行", "研究", "结果", "方法", "我们", "本文",
}


def tokenize(text: str) -> list[str]:
    """English words + CJK character bigrams. Good enough for de-duplication."""
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9\-_.]+", text)
    tokens = [t for t in tokens if t not in _STOP and len(t) > 1]
    cjk = re.findall(r"[一-鿿]", text)
    tokens.extend("".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1)))
    return tokens


def tfidf_vectors(texts: list[str]) -> tuple[list[Counter], dict[str, float]]:
    docs = [Counter(tokenize(t)) for t in texts]
    df: Counter = Counter()
    for doc in docs:
        df.update(set(doc))
    n = max(1, len(docs))
    idf = {term: math.log((1 + n) / (1 + freq)) + 1.0 for term, freq in df.items()}
    vectors: list[Counter] = []
    for doc in docs:
        vec: Counter = Counter()
        for term, count in doc.items():
            vec[term] = (1.0 + math.log(count)) * idf.get(term, 1.0)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        for term in list(vec):
            vec[term] /= norm
        vectors.append(vec)
    return vectors, idf


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def similarity_matrix(texts: list[str]) -> list[list[float]]:
    vectors, _ = tfidf_vectors(texts)
    n = len(vectors)
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        out[i][i] = 1.0
        for j in range(i + 1, n):
            s = cosine(vectors[i], vectors[j])
            out[i][j] = out[j][i] = s
    return out


def top_keywords(texts: list[str], k: int = 8) -> list[str]:
    vectors, _ = tfidf_vectors(texts)
    total: Counter = Counter()
    for vec in vectors:
        total.update(vec)
    return [term for term, _ in total.most_common(k)]


def fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def truncate(text: str, n: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(as_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def clamp(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def mean(values: Iterable[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * (q / 100.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


class Logger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None

    def __call__(self, message: str) -> None:
        line = f"[{now_iso()}] {message}"
        print(line, file=sys.stderr)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")

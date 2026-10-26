"""Proximity graph, de-duplication and diversity helpers.

Mirrors the paper's Proximity agent, but keeps the computation deterministic:
TF-IDF cosine over title + claim + mechanism + keywords.  The LLM still does
semantic clustering in the prompts; these numbers are a cheap second opinion
and a guard against near-identical hypotheses flooding the tournament.
"""
from __future__ import annotations

from collections import defaultdict

from .util import similarity_matrix, top_keywords


def hypothesis_text(h: dict) -> str:
    parts = [
        h.get("title", ""),
        h.get("claim", ""),
        " ".join(map(str, h.get("mechanism") or [])),
        " ".join(map(str, h.get("keywords") or [])),
    ]
    return " ".join(p for p in parts if p)


def cluster_hypotheses(
    hypotheses: list[dict],
    threshold: float = 0.55,
) -> list[dict]:
    """Connected components of the graph ``cosine >= threshold``."""
    if not hypotheses:
        return []
    texts = [hypothesis_text(h) for h in hypotheses]
    sim = similarity_matrix(texts)
    n = len(hypotheses)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if sim[i][j] >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    clusters = []
    for idx, members in enumerate(sorted(groups.values(), key=lambda m: (-len(m), m[0])), 1):
        cid = f"C{idx:02d}"
        clusters.append({
            "cluster": cid,
            "size": len(members),
            "members": [hypotheses[i]["id"] for i in members],
            "keywords": top_keywords([texts[i] for i in members], k=8),
            "mean_similarity": round(
                sum(sim[i][j] for i in members for j in members if i < j)
                / max(1, len(members) * (len(members) - 1) / 2),
                3,
            ),
        })
    return clusters


def near_duplicates(
    hypotheses: list[dict],
    threshold: float = 0.85,
) -> list[dict]:
    if len(hypotheses) < 2:
        return []
    texts = [hypothesis_text(h) for h in hypotheses]
    sim = similarity_matrix(texts)
    pairs = []
    for i in range(len(hypotheses)):
        for j in range(i + 1, len(hypotheses)):
            if sim[i][j] >= threshold:
                pairs.append({
                    "a": hypotheses[i]["id"],
                    "b": hypotheses[j]["id"],
                    "similarity": round(sim[i][j], 3),
                    "title_a": hypotheses[i].get("title", ""),
                    "title_b": hypotheses[j].get("title", ""),
                })
    pairs.sort(key=lambda p: -p["similarity"])
    return pairs


def nearest_prior(text: str, hypotheses: list[dict]) -> tuple[str | None, float]:
    """Best match of a candidate text against existing hypotheses."""
    if not hypotheses:
        return None, 0.0
    sims = similarity_matrix([text] + [hypothesis_text(h) for h in hypotheses])
    row = sims[0][1:]
    if not row:
        return None, 0.0
    best = max(range(len(row)), key=row.__getitem__)
    return hypotheses[best]["id"], float(row[best])


def diversity_score(hypotheses: list[dict], threshold: float = 0.55) -> dict:
    """Fraction of hypotheses that sit in a multi-member cluster + cluster count."""
    clusters = cluster_hypotheses(hypotheses, threshold)
    if not hypotheses:
        return {"n": 0, "clusters": 0, "largest_cluster": 0, "redundancy": 0.0}
    largest = max((c["size"] for c in clusters), default=0)
    in_multi = sum(c["size"] for c in clusters if c["size"] > 1)
    return {
        "n": len(hypotheses),
        "clusters": len(clusters),
        "largest_cluster": largest,
        "redundancy": round(in_multi / len(hypotheses), 3),
    }

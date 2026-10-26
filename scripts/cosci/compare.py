"""Compare two completed runs without pretending it is a controlled A/B test.

Elo is computed inside a single tournament, so raw Elo values are not
comparable across runs.  This module therefore reports *relative* signals:
top-k title overlap, review-score distributions, diversity, strategy mix,
judge-bias checks, and structured external outcomes when available.

A real A/B test still requires the same goal, the same budget, multiple
seeds, and an external metric.  The output says this explicitly.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .ratings import position_bias, verbosity_bias
from .rsi import load_outcomes
from .similarity import diversity_score, hypothesis_text
from .util import mean, truncate


def _title_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _mean_or_none(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def summarize_run(run, top: int = 5) -> dict[str, Any]:
    data = run.load()
    goal = run.load_goal()
    rows = run.leaderboard()
    hypotheses = run.hypotheses(include_archived=False)
    reviews = run.reviews()
    matches = run.matches()
    active = [h for h in hypotheses if h.get("status") != "rejected"]
    diversity = diversity_score(active)
    top_rows = rows[:top]
    top_titles = [_title_key(r.get("title", "")) for r in top_rows]
    strategies = Counter(
        (h.get("provenance") or {}).get("strategy", "unknown") for h in hypotheses
    )
    statuses = Counter(h.get("status", "active") for h in hypotheses)
    outcomes = load_outcomes(run)
    outcome_counts = {
        "n": len(outcomes),
        "supported": sum(1 for v in outcomes.values() if v == "supported"),
        "refuted": sum(1 for v in outcomes.values() if v == "refuted"),
        "inconclusive": sum(1 for v in outcomes.values() if v == "inconclusive"),
        "unknown": sum(1 for v in outcomes.values() if v == "unknown"),
    }
    review_scores = [r["review_score"] for r in rows if r.get("n_reviews")]
    top_review_scores = [r["review_score"] for r in top_rows if r.get("n_reviews")]
    texts = {h["id"]: hypothesis_text(h) for h in hypotheses}
    position = position_bias(matches)
    verbosity = verbosity_bias(matches, texts)
    return {
        "run_id": data.get("run_id"),
        "profile": data.get("profile"),
        "seed": data.get("seed"),
        "phase": data.get("phase"),
        "status": data.get("status"),
        "question": truncate(goal.get("question") or goal.get("title") or "", 240),
        "n_hypotheses": len(hypotheses),
        "n_reviews": len(reviews),
        "n_matches": len(matches),
        "status_counts": dict(statuses),
        "strategy_counts": dict(strategies),
        "diversity": diversity,
        "top": [
            {
                "id": r["id"],
                "title": r.get("title", ""),
                "elo": r.get("elo"),
                "review_score": r.get("review_score"),
                "n_reviews": r.get("n_reviews"),
            }
            for r in top_rows
        ],
        "top_titles": top_titles,
        "review_score_mean": _mean_or_none(review_scores),
        "top_review_score_mean": _mean_or_none(top_review_scores),
        "outcomes": outcome_counts,
        "position_bias": position,
        "verbosity_bias": verbosity,
    }


def compare_runs(run_a, run_b, top: int = 5) -> dict[str, Any]:
    a = summarize_run(run_a, top=top)
    b = summarize_run(run_b, top=top)
    warnings: list[str] = []
    if a["question"] != b["question"]:
        warnings.append("研究问题不同；这不是同一目标的 A/B，结论只能作观察性参考。")
    if a["profile"] != b["profile"]:
        warnings.append(f"profile 不同（{a['profile']} vs {b['profile']}）；预算不可比。")
    if a["seed"] != b["seed"]:
        warnings.append(f"seed 不同（{a['seed']} vs {b['seed']}）；策略采样不可复现。")
    if a["n_hypotheses"] < 5 or b["n_hypotheses"] < 5:
        warnings.append("至少一个 run 的假设数 < 5；任何差异都可能由噪声造成。")
    if a["outcomes"]["n"] == 0 and b["outcomes"]["n"] == 0:
        warnings.append("两个 run 都没有 meta/outcomes.json；质量结论缺少外部真值。")
    warnings.append(
        "Elo 是单次锦标赛内的相对分数，不能跨 run 直接比较；"
        "优先看外部结果、评审分、top-k 重叠和多样性。"
    )
    warnings.append(
        "top-k 标题重叠只衡量表面重合，不是质量指标；两个 run 生成不同但同样好的假设时，重叠会很低。"
    )
    return {
        "schema_version": 1,
        "verdict": "observational comparison, not a controlled A/B test",
        "top_k": top,
        "top_title_overlap": round(
            _jaccard(set(a["top_titles"]), set(b["top_titles"])), 3
        ),
        "run_a": a,
        "run_b": b,
        "warnings": warnings,
    }


def _fmt(value, percent: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}" if percent else str(value)


def compare_text(data: dict) -> str:
    a, b = data["run_a"], data["run_b"]
    lines = [
        "# Co-Scientist run comparison",
        "",
        f"> {data['verdict']}",
        "",
        "| 指标 | A | B |",
        "|---|---:|---:|",
        f"| run_id | `{a['run_id']}` | `{b['run_id']}` |",
        f"| profile | `{a['profile']}` | `{b['profile']}` |",
        f"| 假设数 | {a['n_hypotheses']} | {b['n_hypotheses']} |",
        f"| 评审数 | {a['n_reviews']} | {b['n_reviews']} |",
        f"| 比赛数 | {a['n_matches']} | {b['n_matches']} |",
        f"| 簇数 | {a['diversity']['clusters']} | {b['diversity']['clusters']} |",
        f"| 冗余度 | {_fmt(a['diversity']['redundancy'], True)} | {_fmt(b['diversity']['redundancy'], True)} |",
        f"| 评审均分 | {_fmt(a['review_score_mean'])} | {_fmt(b['review_score_mean'])} |",
        f"| top-{data['top_k']} 评审均分 | {_fmt(a['top_review_score_mean'])} | {_fmt(b['top_review_score_mean'])} |",
        f"| 外部结果（支持/反驳） | {a['outcomes']['supported']}/{a['outcomes']['refuted']} | {b['outcomes']['supported']}/{b['outcomes']['refuted']} |",
        f"| top-{data['top_k']} 标题重叠 | {_fmt(data['top_title_overlap'], True)} | — |",
    ]
    lines += ["", "## 警告", ""]
    lines += [f"- {w}" for w in data["warnings"]]
    lines += ["", "## A 的 top 假设", ""]
    for item in a["top"]:
        lines.append(
            f"- #{item['id']} {item['title']} "
            f"(Elo {_fmt(item['elo'])}, Rev {_fmt(item['review_score'])})"
        )
    lines += ["", "## B 的 top 假设", ""]
    for item in b["top"]:
        lines.append(
            f"- #{item['id']} {item['title']} "
            f"(Elo {_fmt(item['elo'])}, Rev {_fmt(item['review_score'])})"
        )
    return "\n".join(lines)

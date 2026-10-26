"""Elo, bootstrap confidence intervals, and Bradley-Terry ranking.

The paper uses Elo for its tournament.  We keep Elo as the online rating
(cheap, interpretable, order-independent when the judge is order-randomised)
and add two robustness layers:

* bootstrap confidence intervals over the match log;
* a Bradley-Terry maximum-likelihood ranking as a second opinion.

Both are deterministic given the match log, so a run can be replayed.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

from .util import mean, percentile, sigmoid


INITIAL_RATING = 1200.0


def k_factor(matches_played: int) -> float:
    if matches_played < 10:
        return 32.0
    if matches_played < 30:
        return 24.0
    return 16.0


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def replay_elo(matches: list[dict], initial: float = INITIAL_RATING) -> tuple[dict[str, float], dict[str, int]]:
    ratings: dict[str, float] = {}
    counts: dict[str, int] = defaultdict(int)
    for m in matches:
        a, b, winner = m.get("a"), m.get("b"), m.get("winner")
        if not a or not b or winner not in (a, b, "draw"):
            continue
        ratings.setdefault(a, initial)
        ratings.setdefault(b, initial)
        ra, rb = ratings[a], ratings[b]
        ea = expected_score(ra, rb)
        if winner == "draw":
            sa = 0.5
        elif winner == a:
            sa = 1.0
        else:
            sa = 0.0
        ka, kb = k_factor(counts[a]), k_factor(counts[b])
        ratings[a] = ra + ka * (sa - ea)
        ratings[b] = rb + kb * ((1.0 - sa) - (1.0 - ea))
        counts[a] += 1
        counts[b] += 1
    return ratings, dict(counts)


def bootstrap_ci(
    matches: list[dict],
    iterations: int = 1000,
    seed: int = 42,
    initial: float = INITIAL_RATING,
) -> dict[str, tuple[float, float]]:
    """Resample matches with replacement, replay Elo, report 2.5/97.5 pct."""
    if not matches:
        return {}
    rng = random.Random(seed)
    draws: dict[str, list[float]] = defaultdict(list)
    for _ in range(iterations):
        sample = [matches[rng.randrange(len(matches))] for _ in matches]
        ratings, _ = replay_elo(sample, initial=initial)
        for hid, rating in ratings.items():
            draws[hid].append(rating)
    return {
        hid: (percentile(vals, 2.5), percentile(vals, 97.5))
        for hid, vals in draws.items()
        if vals
    }


def bradley_terry(
    matches: list[dict],
    iterations: int = 800,
    lr: float = 0.05,
    l2: float = 0.1,
) -> dict[str, float]:
    """Fit a Bradley-Terry model with gradient ascent.

    Returns log-strength theta.  Convert to an Elo-like scale with
    ``1200 + 400/ln(10) * theta`` if a familiar number is wanted.
    """
    ids = sorted({m[k] for m in matches for k in ("a", "b") if m.get(k)})
    if not ids:
        return {}
    theta = {hid: 0.0 for hid in ids}
    for _ in range(iterations):
        grad = {hid: -l2 * theta[hid] for hid in ids}
        for m in matches:
            a, b, winner = m.get("a"), m.get("b"), m.get("winner")
            if a not in theta or b not in theta or winner not in (a, b, "draw"):
                continue
            p = sigmoid(theta[a] - theta[b])
            score = 0.5 if winner == "draw" else (1.0 if winner == a else 0.0)
            diff = score - p
            grad[a] += diff
            grad[b] -= diff
        for hid in ids:
            theta[hid] += lr * grad[hid]
    centre = mean(theta.values())
    return {hid: value - centre for hid, value in theta.items()}


def bt_to_elo(theta: float, initial: float = INITIAL_RATING) -> float:
    return initial + (400.0 / math.log(10.0)) * theta


def _match_counts(matches: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0, "draws": 0})
    for m in matches:
        a, b, winner = m.get("a"), m.get("b"), m.get("winner")
        if winner == "draw":
            out[a]["draws"] += 1
            out[b]["draws"] += 1
        elif winner == a:
            out[a]["wins"] += 1
            out[b]["losses"] += 1
        elif winner == b:
            out[b]["wins"] += 1
            out[a]["losses"] += 1
    return dict(out)


def position_bias(matches: list[dict]) -> dict[str, Any]:
    """How often did the hypothesis shown first win?

    A fair judge should be near 50%.  A large deviation suggests position bias;
    the skill then swaps sides for the next match involving those hypotheses.
    """
    n = first_wins = 0
    n_total = len(matches)
    for m in matches:
        order = m.get("order_presented") or []
        if len(order) != 2 or m.get("winner") not in order:
            continue
        n += 1
        if m["winner"] == order[0]:
            first_wins += 1
    if n == 0:
        return {"n": 0, "n_total": n_total, "coverage": 0.0,
                "first_win_rate": None, "z": None, "p": None}
    rate = first_wins / n
    z = (first_wins - n / 2.0) / math.sqrt(n * 0.25)
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return {"n": n, "n_total": n_total, "coverage": n / n_total if n_total else 0.0,
            "first_wins": first_wins, "first_win_rate": rate, "z": z, "p": p}


def verbosity_bias(matches: list[dict], texts: dict[str, str]) -> dict[str, Any]:
    """Compare winner vs loser text length.  Ratio > 1.15 is worth flagging."""
    win_lengths: list[int] = []
    loss_lengths: list[int] = []
    for m in matches:
        a, b, winner = m.get("a"), m.get("b"), m.get("winner")
        if winner not in (a, b):
            continue
        loser = b if winner == a else a
        win_lengths.append(len(texts.get(winner, "")))
        loss_lengths.append(len(texts.get(loser, "")))
    if not win_lengths:
        return {"n": 0}
    w, l = mean(win_lengths), mean(loss_lengths)
    return {
        "n": len(win_lengths),
        "mean_winner_chars": w,
        "mean_loser_chars": l,
        "ratio": (w / l) if l else None,
        "flag": bool(l and w / l > 1.15),
    }


def _weighted_review_score(scores: dict, criteria: list[dict] | None) -> float | None:
    """Apply the goal's criterion weights, falling back to an unweighted mean.

    Only dimensions present in the review are used; the remaining weights are
    renormalised, so a missing optional dimension does not turn into a zero.
    """
    vals = {
        str(k).strip().lower(): float(v)
        for k, v in (scores or {}).items()
        if isinstance(v, (int, float))
    }
    if not vals:
        return None
    weights: dict[str, float] = {}
    for criterion in criteria or []:
        if not isinstance(criterion, dict):
            continue
        name = str(criterion.get("name", "")).strip().lower()
        if name not in vals:
            continue
        try:
            weight = float(criterion.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if weight > 0:
            weights[name] = weight
    if weights:
        total = sum(weights.values())
        return sum(weights[name] * vals[name] for name in weights) / total
    return mean(vals.values())


def build_leaderboard(
    hypotheses: list[dict],
    matches: list[dict],
    reviews: list[dict] | None = None,
    bootstrap_iterations: int = 1000,
    seed: int = 42,
    criteria: list[dict] | None = None,
) -> list[dict]:
    """Return rows sorted by Elo, enriched with CIs, W/L/D, review averages."""
    ratings, counts = replay_elo(matches)
    cis = bootstrap_ci(matches, iterations=bootstrap_iterations, seed=seed)
    theta = bradley_terry(matches)
    counts_detail = _match_counts(matches)

    review_scores: dict[str, list[float]] = defaultdict(list)
    review_means: dict[str, list[float]] = defaultdict(list)
    for r in reviews or []:
        hid = r.get("hypothesis_id")
        scores = r.get("scores") or {}
        if hid and isinstance(scores, dict):
            weighted = _weighted_review_score(scores, criteria)
            vals = [float(v) for v in scores.values() if isinstance(v, (int, float))]
            if weighted is not None:
                review_scores[hid].append(weighted)
            if vals:
                review_means[hid].append(mean(vals))

    rows = []
    for h in hypotheses:
        hid = h["id"]
        rating = ratings.get(hid, INITIAL_RATING)
        ci = cis.get(hid)
        detail = counts_detail.get(hid, {"wins": 0, "losses": 0, "draws": 0})
        rows.append({
            "id": hid,
            "title": h.get("title", ""),
            "status": h.get("status", "active"),
            "cluster": h.get("cluster", ""),
            "strategy": (h.get("provenance") or {}).get("strategy", ""),
            "elo": round(rating, 1),
            "elo_ci_low": round(ci[0], 1) if ci else None,
            "elo_ci_high": round(ci[1], 1) if ci else None,
            "bt_theta": round(theta.get(hid, 0.0), 3),
            "bt_elo": round(bt_to_elo(theta.get(hid, 0.0)), 1),
            "matches": counts.get(hid, 0),
            "wins": detail["wins"],
            "losses": detail["losses"],
            "draws": detail["draws"],
            "review_score": round(mean(review_scores.get(hid, [])), 2),
            "review_mean": round(mean(review_means.get(hid, [])), 2),
            "n_reviews": len(review_scores.get(hid, [])),
        })
    rows.sort(key=lambda r: (-r["elo"], -r["bt_elo"], r["id"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def rank_text(rows: list[dict], top: int | None = None) -> str:
    """Plain-text table; no external table library required."""
    shown = rows[:top] if top else rows
    header = f"{'#':>3}  {'ID':<6} {'Elo':>6} {'95% CI':>15} {'BT*':>6} {'M':>3} {'W-L-D':>8} {'Rev':>4}  Title"
    lines = [header, "-" * min(120, len(header) + 40)]
    for r in shown:
        ci = f"{r['elo_ci_low']:.0f}-{r['elo_ci_high']:.0f}" if r["elo_ci_low"] is not None else "-"
        wld = f"{r['wins']}-{r['losses']}-{r['draws']}"
        lines.append(
            f"{r['rank']:>3}  {r['id']:<6} {r['elo']:>6.1f} {ci:>15} "
            f"{r['bt_elo']:>6.1f} {r['matches']:>3} {wld:>8} {r['review_score']:>4.1f}  {r['title'][:60]}"
        )
    lines.append("")
    lines.append("BT* = Bradley-Terry 强度（第二意见；小样本时端点会偏极端，不要与 Elo 直接比数值）。")
    lines.append("Rev = 按 goal.json.evaluation_criteria 权重计算的加权评审分（缺失维度会重新归一化）。")
    return "\n".join(lines)

"""File-backed state for a Co-Scientist run.

Everything the run knows lives on disk.  This is deliberate: subagents can
write into ``inbox/`` without sharing context, the main agent ingests their
JSON, and the whole run can be resumed, audited, or replayed later.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .ratings import INITIAL_RATING, build_leaderboard, replay_elo
from .similarity import (
    cluster_hypotheses,
    diversity_score,
    hypothesis_text,
    near_duplicates,
    nearest_prior,
)
from .util import (
    Logger,
    as_list,
    as_text,
    clamp,
    fingerprint,
    now_iso,
    read_json,
    read_text,
    safe_slug,
    truncate,
    write_json,
    write_text,
)


PROFILES: dict[str, dict[str, Any]] = {
    "quick": {
        "hypotheses": 8,
        "generation_agents": 3,
        "matches": 16,
        "evolution_rounds": 1,
        "reflection_batches": 2,
        "max_subagents": 8,
        "bootstrap_iterations": 500,
        "description": "1 次快速选题会：8 个假设、16 场比较、1 轮进化。",
    },
    "standard": {
        "hypotheses": 18,
        "generation_agents": 6,
        "matches": 60,
        "evolution_rounds": 2,
        "reflection_batches": 3,
        "max_subagents": 18,
        "bootstrap_iterations": 1000,
        "description": "默认：18 个假设、60 场比较、2 轮进化。",
    },
    "deep": {
        "hypotheses": 40,
        "generation_agents": 8,
        "matches": 160,
        "evolution_rounds": 3,
        "reflection_batches": 4,
        "max_subagents": 32,
        "bootstrap_iterations": 2000,
        "description": "高强度：40+ 假设、160+ 比较、3 轮进化；需分阶段确认。",
    },
}

HYPOTHESIS_STATUS = {"active", "needs_revision", "rejected", "archived", "merged"}
REVIEW_TYPES = {"triage", "full", "deep", "observation", "simulation", "redteam", "calibration", "human"}
VERDICTS = {"pass", "revise", "reject", "abstain"}


class RunError(RuntimeError):
    pass


def _normalise_scores(scores: Any) -> dict[str, float]:
    if not isinstance(scores, dict):
        return {}
    out = {}
    for key, value in scores.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        out[str(key)] = round(clamp(number), 2)
    return out


def _normalise_hypothesis(raw: dict, run_id: str) -> dict:
    h = dict(raw or {})
    title = as_text(h.get("title") or h.get("name")).strip()
    claim = as_text(h.get("claim") or h.get("hypothesis") or h.get("statement")).strip()
    if not title:
        raise RunError("hypothesis.title is required")
    if not claim:
        claim = as_text(h.get("summary") or h.get("description")).strip()
    if not claim:
        raise RunError(f"hypothesis {title!r}: claim/statement is required")

    h["title"] = title
    h["claim"] = claim
    h["summary"] = as_text(h.get("summary") or claim)
    h["mechanism"] = [as_text(x) for x in as_list(h.get("mechanism")) if as_text(x)]
    raw_predictions = as_list(h.get("predictions"))
    h["predictions"] = []
    for p in raw_predictions:
        if isinstance(p, dict):
            h["predictions"].append(p)
        elif as_text(p):
            h["predictions"].append({"if_true": as_text(p)})
    raw_evidence = as_list(h.get("evidence"))
    h["evidence"] = []
    for e in raw_evidence:
        if isinstance(e, dict):
            item = dict(e)
        elif as_text(e):
            item = {"source": as_text(e)}
        else:
            continue
        item.setdefault("supports", "unknown")
        item.setdefault("verified", False)
        h["evidence"].append(item)
    raw_assumptions = as_list(h.get("assumptions"))
    h["assumptions"] = []
    for a in raw_assumptions:
        if isinstance(a, dict):
            item = dict(a)
        elif as_text(a):
            item = {"text": as_text(a)}
        else:
            continue
        item.setdefault("load_bearing", True)
        item.setdefault("status", "untested")
        h["assumptions"].append(item)
    h["risks"] = [as_text(x) for x in as_list(h.get("risks")) if as_text(x)]
    h["keywords"] = [as_text(x) for x in as_list(h.get("keywords")) if as_text(x)]
    for key in ("novelty", "testability", "impact", "feasibility", "safety"):
        value = h.get(key)
        if isinstance(value, dict):
            if "score" in value:
                try:
                    value["score"] = round(clamp(float(value["score"])), 2)
                except (TypeError, ValueError):
                    value.pop("score", None)
        elif isinstance(value, (int, float, str)):
            try:
                h[key] = {"score": round(clamp(float(value)), 2)}
            except (TypeError, ValueError):
                h[key] = {}
        else:
            h[key] = {}
    provenance = h.get("provenance") if isinstance(h.get("provenance"), dict) else {}
    provenance.setdefault("agent", "unknown")
    provenance.setdefault("strategy", "unknown")
    provenance.setdefault("parent_ids", [])
    provenance.setdefault("round", 1)
    provenance["run_id"] = run_id
    h["provenance"] = provenance
    h.setdefault("status", "active")
    if h["status"] not in HYPOTHESIS_STATUS:
        h["status"] = "active"
    h.setdefault("created_at", now_iso())
    h["updated_at"] = now_iso()
    return h


class Run:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.run_file = self.root / "run.json"
        self.goal_file = self.root / "goal.json"
        self.tournament_file = self.root / "tournament.json"
        self.index_file = self.root / "hypotheses" / "index.json"
        self.hyp_dir = self.root / "hypotheses"
        self.review_dir = self.root / "reviews"
        self.inbox_dir = self.root / "inbox"
        self.prompt_dir = self.root / "prompts"
        self.meta_dir = self.root / "meta"
        self.evolution_dir = self.root / "evolution"
        self.log = Logger(self.root / "run.log")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        root: str | Path,
        goal: dict,
        profile: str = "standard",
        seed: int = 42,
        force: bool = False,
    ) -> "Run":
        if profile not in PROFILES:
            raise RunError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
        run = cls(root)
        if run.root.exists() and any(run.root.iterdir()):
            if not force:
                raise RunError(f"run directory already exists and is not empty: {run.root}")
            shutil.rmtree(run.root)
        for d in (run.hyp_dir, run.review_dir, run.inbox_dir, run.prompt_dir,
                  run.meta_dir, run.evolution_dir):
            d.mkdir(parents=True, exist_ok=True)

        title = as_text(goal.get("title") or goal.get("question") or "Untitled research goal")
        run_id = run.root.name
        run_data = {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "profile": profile,
            "seed": seed,
            "phase": "0_intake",
            "status": "initialized",
            "goal_file": "goal.json",
            "budgets": dict(PROFILES[profile]),
            "counters": {
                "hypotheses": 0,
                "reviews": 0,
                "matches": 0,
                "evolution_rounds": 0,
            },
            "history": [{"at": now_iso(), "event": "init", "detail": title}],
        }
        write_json(run.run_file, run_data)
        write_json(run.goal_file, goal)
        write_json(run.tournament_file, {
            "schema_version": 1,
            "initial_rating": INITIAL_RATING,
            "matches": [],
            "next_match": 1,
        })
        write_json(run.index_file, {
            "schema_version": 1,
            "next_id": 1,
            "clusters": 0,
            "hypotheses": {},
        })
        run.log(f"initialized {run_id} profile={profile}")
        return run

    def exists(self) -> bool:
        return self.run_file.exists()

    def load(self) -> dict:
        if not self.run_file.exists():
            raise RunError(f"not a Co-Scientist run (missing run.json): {self.root}")
        return read_json(self.run_file)

    def load_goal(self) -> dict:
        if not self.goal_file.exists():
            return {}
        return read_json(self.goal_file)

    def save(self, data: dict) -> None:
        data["updated_at"] = now_iso()
        write_json(self.run_file, data)

    def touch_phase(self, phase: str, status: str | None = None, detail: str = "") -> None:
        data = self.load()
        data["phase"] = phase
        if status:
            data["status"] = status
        data.setdefault("history", []).append(
            {"at": now_iso(), "event": f"phase:{phase}", "detail": detail}
        )
        self.save(data)
        self.log(f"phase={phase} status={data.get('status')} {detail}".strip())

    # ------------------------------------------------------------------
    # raw stores
    # ------------------------------------------------------------------
    def index(self) -> dict:
        if not self.index_file.exists():
            return {"schema_version": 1, "next_id": 1, "clusters": 0, "hypotheses": {}}
        return read_json(self.index_file)

    def save_index(self, index: dict) -> None:
        write_json(self.index_file, index)

    def tournament(self) -> dict:
        if not self.tournament_file.exists():
            return {"schema_version": 1, "initial_rating": INITIAL_RATING,
                    "matches": [], "next_match": 1}
        return read_json(self.tournament_file)

    def save_tournament(self, data: dict) -> None:
        write_json(self.tournament_file, data)

    def hypotheses(self, include_archived: bool = True) -> list[dict]:
        index = self.index()
        out = []
        for hid, entry in index.get("hypotheses", {}).items():
            path = self.root / entry.get("file", f"hypotheses/{hid}.json")
            if path.exists():
                h = read_json(path)
                h.setdefault("id", hid)
                out.append(h)
        if not include_archived:
            out = [h for h in out if h.get("status") != "archived"]
        return out

    def get_hypothesis(self, hid: str) -> dict:
        index = self.index()
        entry = index.get("hypotheses", {}).get(hid)
        if not entry:
            raise RunError(f"unknown hypothesis: {hid}")
        path = self.root / entry.get("file", f"hypotheses/{hid}.json")
        if not path.exists():
            raise RunError(f"hypothesis file missing: {path}")
        return read_json(path)

    def reviews(self, hid: str | None = None) -> list[dict]:
        out = []
        for path in sorted(self.review_dir.glob("*.json")):
            try:
                r = read_json(path)
            except Exception:
                continue
            if hid is None or r.get("hypothesis_id") == hid:
                out.append(r)
        return out

    def matches(self) -> list[dict]:
        return list(self.tournament().get("matches", []))

    # ------------------------------------------------------------------
    # hypotheses
    # ------------------------------------------------------------------
    def add_hypotheses(
        self,
        items: list[dict],
        strategy: str | None = None,
        agent: str | None = None,
        parent_ids: list[str] | None = None,
        round_no: int | None = None,
    ) -> dict:
        index = self.index()
        existing = self.hypotheses()
        by_fp = {entry.get("fingerprint"): hid for hid, entry in index.get("hypotheses", {}).items()}
        added, skipped = [], []

        for raw in items:
            if not isinstance(raw, dict):
                skipped.append({"reason": "not an object", "raw": truncate(str(raw))})
                continue
            provenance = dict(raw.get("provenance") or {})
            if strategy:
                provenance["strategy"] = strategy
            if agent:
                provenance["agent"] = agent
            if parent_ids:
                provenance["parent_ids"] = parent_ids
            if round_no is not None:
                provenance["round"] = round_no
            raw = {**raw, "provenance": provenance}
            try:
                h = _normalise_hypothesis(raw, self.load()["run_id"])
            except RunError as exc:
                skipped.append({"reason": str(exc), "title": raw.get("title")})
                continue

            text = hypothesis_text(h)
            fp = fingerprint(text)
            if fp in by_fp:
                skipped.append({
                    "reason": "duplicate fingerprint",
                    "duplicate_of": by_fp[fp],
                    "title": h["title"],
                })
                continue

            nearest, sim = nearest_prior(text, existing)
            if nearest and sim >= 0.72:
                cluster = next(
                    (e.get("cluster") for e in index["hypotheses"].values() if e.get("id") == nearest),
                    None,
                )
            else:
                cluster = None
            if not cluster:
                index["clusters"] = int(index.get("clusters", 0)) + 1
                cluster = f"C{index['clusters']:02d}"

            # Always assign a canonical sequential ID.  Model-provided IDs are
            # kept only as provenance; they are not trusted as unique keys.
            source_id = h.get("id")
            hid = f"H{int(index.get('next_id', 1)):03d}"
            if hid in index["hypotheses"]:
                skipped.append({"reason": "id already exists", "id": hid})
                continue
            index["next_id"] = int(index.get("next_id", 1)) + 1
            if source_id and str(source_id) != hid:
                h["provenance"]["source_id"] = str(source_id)
            h["id"] = hid
            h["cluster"] = cluster
            h["nearest_prior"] = {"id": nearest, "similarity": round(sim, 3)} if nearest else None

            path = self.hyp_dir / f"{hid}.json"
            write_json(path, h)
            index["hypotheses"][hid] = {
                "id": hid,
                "file": f"hypotheses/{hid}.json",
                "title": h["title"],
                "fingerprint": fp,
                "cluster": cluster,
                "strategy": h["provenance"].get("strategy", "unknown"),
                "parents": h["provenance"].get("parent_ids", []),
                "created_at": h["created_at"],
                "status": h["status"],
                "nearest": nearest,
                "max_similarity": round(sim, 3),
                "reviews": [],
                "wins": 0,
                "losses": 0,
                "draws": 0,
            }
            by_fp[fp] = hid
            existing.append(h)
            added.append({"id": hid, "title": h["title"], "cluster": cluster,
                          "nearest": nearest, "similarity": round(sim, 3)})

        if added:
            data = self.load()
            data["counters"]["hypotheses"] = len(index["hypotheses"])
            data["history"].append({"at": now_iso(), "event": "add_hypotheses", "detail": f"{len(added)} added"})
            self.save(data)
        self.save_index(index)
        return {"added": added, "skipped": skipped, "total": len(index["hypotheses"])}

    def set_hypothesis_status(self, hid: str, status: str) -> None:
        if status not in HYPOTHESIS_STATUS:
            raise RunError(f"invalid status {status!r}")
        h = self.get_hypothesis(hid)
        h["status"] = status
        h["updated_at"] = now_iso()
        write_json(self.hyp_dir / f"{hid}.json", h)
        index = self.index()
        if hid in index["hypotheses"]:
            index["hypotheses"][hid]["status"] = status
            self.save_index(index)

    # ------------------------------------------------------------------
    # reviews
    # ------------------------------------------------------------------
    def add_reviews(self, items: list[dict], reviewer: str | None = None) -> dict:
        added, skipped = [], []
        index = self.index()
        for raw in items:
            if not isinstance(raw, dict):
                skipped.append({"reason": "not an object"})
                continue
            hid = raw.get("hypothesis_id") or raw.get("target") or raw.get("id")
            hid = str(hid) if hid is not None else None
            if hid not in index.get("hypotheses", {}):
                skipped.append({"reason": f"unknown hypothesis {hid!r}"})
                continue
            rtype = raw.get("review_type") or raw.get("type") or "full"
            if rtype not in REVIEW_TYPES:
                rtype = "full"
            verdict = raw.get("verdict") or raw.get("decision") or "abstain"
            if verdict not in VERDICTS:
                verdict = "abstain"
            review = dict(raw)
            review["hypothesis_id"] = hid
            review["review_type"] = rtype
            review["verdict"] = verdict
            review["scores"] = _normalise_scores(raw.get("scores"))
            review.setdefault("reviewed_at", now_iso())
            review["reviewer"] = reviewer or raw.get("reviewer") or raw.get("agent") or "unknown"
            count = len(list(self.review_dir.glob(f"{hid}.{rtype}.*.json"))) + 1
            path = self.review_dir / f"{hid}.{rtype}.{count}.json"
            write_json(path, review)

            entry = index["hypotheses"][hid]
            entry.setdefault("reviews", []).append({
                "file": f"reviews/{path.name}",
                "type": rtype,
                "verdict": verdict,
                "score": round(sum(review["scores"].values()) / len(review["scores"]), 2)
                if review["scores"] else None,
            })
            if verdict == "reject":
                entry["status"] = "rejected"
                h = self.get_hypothesis(hid)
                h["status"] = "rejected"
                h["updated_at"] = now_iso()
                write_json(self.hyp_dir / f"{hid}.json", h)
            elif verdict == "revise":
                entry["status"] = "needs_revision"
                h = self.get_hypothesis(hid)
                h["status"] = "needs_revision"
                h["updated_at"] = now_iso()
                write_json(self.hyp_dir / f"{hid}.json", h)
            elif verdict == "pass" and entry.get("status") in ("needs_revision", "active"):
                entry["status"] = "active"
            added.append({"hypothesis_id": hid, "review_type": rtype,
                          "verdict": verdict, "file": str(path.relative_to(self.root))})

        if added:
            data = self.load()
            data["counters"]["reviews"] = sum(
                len(e.get("reviews", [])) for e in index["hypotheses"].values()
            )
            data["history"].append({"at": now_iso(), "event": "add_reviews", "detail": f"{len(added)} added"})
            self.save(data)
        self.save_index(index)
        return {"added": added, "skipped": skipped,
                "total_reviews": sum(len(e.get("reviews", [])) for e in index["hypotheses"].values())}

    # ------------------------------------------------------------------
    # matches
    # ------------------------------------------------------------------
    def add_matches(self, items: list[dict], judge: str | None = None) -> dict:
        added, skipped = [], []
        tournament = self.tournament()
        index = self.index()
        known = set(index.get("hypotheses", {}))
        for raw in items:
            if not isinstance(raw, dict):
                skipped.append({"reason": "not an object"})
                continue
            a = str(raw.get("a")) if raw.get("a") is not None else None
            b = str(raw.get("b")) if raw.get("b") is not None else None
            order = [str(x) for x in (raw.get("order_presented") or []) if x is not None]
            # Judges sometimes omit a/b and only return order_presented.  Recover
            # them so a correct judgement is not silently dropped.
            if len(order) == 2:
                oa, ob = order
                if a is None and b is None:
                    a, b = oa, ob
                elif a is None and b in (oa, ob):
                    a = ob if b == oa else oa
                elif b is None and a in (oa, ob):
                    b = ob if a == oa else oa
            winner = raw.get("winner")
            if winner is not None:
                winner = str(winner)
            if winner in ("A", "a") and a:
                winner = a
            elif winner in ("B", "b") and b:
                winner = b
            if a not in known or b not in known:
                skipped.append({"reason": f"unknown hypothesis in match {a!r} vs {b!r}"})
                continue
            if a == b:
                skipped.append({"reason": "a and b are the same"})
                continue
            if winner not in (a, b, "draw"):
                skipped.append({"reason": f"invalid winner {winner!r}"})
                continue

            before, _ = replay_elo(tournament["matches"])
            ra = before.get(a, tournament.get("initial_rating", INITIAL_RATING))
            rb = before.get(b, tournament.get("initial_rating", INITIAL_RATING))
            match = dict(raw)
            match_id = str(raw.get("id") or raw.get("match_id")
                           or f"M{int(tournament.get('next_match', 1)):04d}")
            if any(m.get("id") == match_id for m in tournament["matches"]):
                skipped.append({"reason": f"match id already exists: {match_id}"})
                continue
            match["id"] = match_id
            match["a"], match["b"], match["winner"] = a, b, winner
            order = raw.get("order_presented") or [a, b]
            if set(order) != {a, b}:
                order = [a, b]
            match["order_presented"] = order
            if isinstance(raw.get("confidence"), (int, float)):
                match["confidence"] = round(clamp(float(raw["confidence"]), 0.0, 1.0), 2)
            match["judge"] = judge or raw.get("judge") or "ranking"
            match["judged_at"] = now_iso()
            tournament["matches"].append(match)
            try:
                match_num = int("".join(ch for ch in match["id"] if ch.isdigit()) or 0)
            except ValueError:
                match_num = 0
            tournament["next_match"] = max(int(tournament.get("next_match", 1)), match_num + 1)
            after, _ = replay_elo(tournament["matches"])
            match["elo_before"] = {a: round(ra, 1), b: round(rb, 1)}
            match["elo_after"] = {a: round(after.get(a, ra), 1), b: round(after.get(b, rb), 1)}

            entry_a, entry_b = index["hypotheses"][a], index["hypotheses"][b]
            if winner == "draw":
                entry_a["draws"] = int(entry_a.get("draws", 0)) + 1
                entry_b["draws"] = int(entry_b.get("draws", 0)) + 1
            elif winner == a:
                entry_a["wins"] = int(entry_a.get("wins", 0)) + 1
                entry_b["losses"] = int(entry_b.get("losses", 0)) + 1
            else:
                entry_b["wins"] = int(entry_b.get("wins", 0)) + 1
                entry_a["losses"] = int(entry_a.get("losses", 0)) + 1
            added.append({"id": match["id"], "a": a, "b": b, "winner": winner,
                          "elo_after": match["elo_after"]})

        if added:
            self.save_tournament(tournament)
            data = self.load()
            data["counters"]["matches"] = len(tournament["matches"])
            data["history"].append({"at": now_iso(), "event": "add_matches", "detail": f"{len(added)} added"})
            self.save(data)
        self.save_index(index)
        return {"added": added, "skipped": skipped, "total_matches": len(tournament["matches"])}

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def leaderboard(
        self,
        bootstrap_iterations: int | None = None,
        include_rejected: bool = False,
    ) -> list[dict]:
        data = self.load()
        n = bootstrap_iterations or int(data.get("budgets", {}).get("bootstrap_iterations", 1000))
        hypotheses = self.hypotheses(include_archived=False)
        if not include_rejected:
            hypotheses = [h for h in hypotheses if h.get("status") != "rejected"]
        goal = self.load_goal()
        return build_leaderboard(
            hypotheses,
            self.matches(),
            self.reviews(),
            bootstrap_iterations=n,
            seed=int(data.get("seed", 42)),
            criteria=goal.get("evaluation_criteria") or [],
        )

    def select(self, top: int = 5, diverse: int = 2) -> list[dict]:
        rows = self.leaderboard()
        if not rows:
            return []
        selected = [r for r in rows[:top]]
        chosen_clusters = {r["cluster"] for r in selected}
        for row in rows:
            if len(selected) >= top + diverse:
                break
            if row["cluster"] not in chosen_clusters:
                selected.append(row)
                chosen_clusters.add(row["cluster"])
        return selected

    def status(self) -> dict:
        data = self.load()
        index = self.index()
        hs = self.hypotheses(include_archived=False)
        reviews = self.reviews()
        matches = self.matches()
        reviewed = {r.get("hypothesis_id") for r in reviews}
        unverified = sum(
            1 for h in hs for e in (h.get("evidence") or [])
            if isinstance(e, dict) and not e.get("verified")
        )
        div = diversity_score(hs)
        leaderboard = self.leaderboard(bootstrap_iterations=200)
        return {
            "run_id": data.get("run_id"),
            "profile": data.get("profile"),
            "phase": data.get("phase"),
            "status": data.get("status"),
            "counters": data.get("counters", {}),
            "hypotheses": len(hs),
            "clusters": div["clusters"],
            "redundancy": div["redundancy"],
            "reviewed": len(reviewed & {h["id"] for h in hs}),
            "unreviewed": sorted({h["id"] for h in hs} - reviewed),
            "reviews": len(reviews),
            "matches": len(matches),
            "unverified_citations": unverified,
            "top5": [
                {"id": r["id"], "title": r["title"], "elo": r["elo"],
                 "matches": r["matches"], "review_score": r["review_score"]}
                for r in leaderboard[:5]
            ],
            "warnings": self.warnings(),
        }

    def warnings(self) -> list[str]:
        out = []
        hs = self.hypotheses(include_archived=False)
        reviews = self.reviews()
        reviewed = {r.get("hypothesis_id") for r in reviews}
        for h in hs:
            if h["id"] not in reviewed:
                out.append(f"{h['id']} 尚未评审")
            if not h.get("predictions"):
                out.append(f"{h['id']} 缺少可证伪预测")
            if not (h.get("testability") or {}).get("minimal_experiment"):
                out.append(f"{h['id']} 缺少最小验证方案")
        for h in hs:
            for e in h.get("evidence") or []:
                if isinstance(e, dict) and not e.get("verified") and e.get("doi"):
                    out.append(f"{h['id']} 引文未核验: {e.get('doi')}")
        dups = near_duplicates(hs)
        for p in dups[:3]:
            out.append(f"近重复: {p['a']} ~ {p['b']} ({p['similarity']})")
        return out

    def recluster(self, threshold: float = 0.55) -> list[dict]:
        hs = self.hypotheses()
        clusters = cluster_hypotheses(hs, threshold)
        index = self.index()
        for c in clusters:
            for hid in c["members"]:
                if hid in index["hypotheses"]:
                    index["hypotheses"][hid]["cluster"] = c["cluster"]
                h = self.get_hypothesis(hid)
                h["cluster"] = c["cluster"]
                write_json(self.hyp_dir / f"{hid}.json", h)
        index["clusters"] = len(clusters)
        self.save_index(index)
        return clusters

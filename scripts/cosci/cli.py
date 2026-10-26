"""Command line interface for the Co-Scientist skill."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from .compare import compare_runs, compare_text
from .ratings import build_leaderboard, rank_text
from .report import build_report
from .rsi import (
    DEFAULT_PLAYBOOK_DIR,
    ensure_playbook,
    normalize_outcome,
    rollback,
    status as rsi_status,
    suggest_strategies,
    update_from_run,
)
from .similarity import cluster_hypotheses, diversity_score, near_duplicates
from .state import PROFILES, Run, RunError
from .util import (
    as_text,
    force_utf8_stdout,
    load_jsonl,
    now_iso,
    read_json,
    read_json_loose,
    read_text,
    safe_slug,
    today,
    truncate,
    write_json,
    write_text,
)


SKILL_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = SKILL_ROOT / "references" / "prompts"
ASSET_DIR = SKILL_ROOT / "assets"

JSON_PROMPTS = {
    "supervisor", "generation", "reflection", "debate_judge",
    "evolution", "rsi_retrospective", "calibration",
}
NARRATIVE_PROMPTS = {
    "landscape", "meta_review", "final_synthesis", "human_gate",
}


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _load_goal(path: Path) -> dict:
    if not path.exists():
        die(f"goal file not found: {path}")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            die("PyYAML is required for YAML goals; use JSON or `pip install pyyaml`")
        with path.open("r", encoding="utf-8-sig") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            die("goal YAML must contain a mapping")
        return data
    if path.suffix.lower() == ".json":
        data = read_json(path)
        if not isinstance(data, dict):
            die("goal JSON must contain an object")
        return data
    raw = read_text(path)
    first = next((ln.strip("# ").strip() for ln in raw.splitlines() if ln.strip()), "Untitled research goal")
    return {
        "title": first[:120],
        "question": raw.strip(),
        "raw": raw.strip(),
        "constraints": {},
        "evaluation_criteria": [
            {"name": "novelty", "weight": 0.25, "rubric": "相对最近先行工作的增量"},
            {"name": "correctness", "weight": 0.25, "rubric": "机制自洽、证据支持、无致命漏洞"},
            {"name": "testability", "weight": 0.20, "rubric": "能否在给定资源内得到支持/反证"},
            {"name": "impact", "weight": 0.20, "rubric": "对领域理解或实践的价值"},
            {"name": "feasibility", "weight": 0.10, "rubric": "时间、人员、平台、数据可行性"},
        ],
    }


def cmd_init(args: argparse.Namespace) -> None:
    goal = _load_goal(Path(args.goal).expanduser())
    if args.title:
        goal["title"] = args.title
    if args.raw_text:
        goal["raw"] = args.raw_text
        goal.setdefault("question", args.raw_text)
    out = Path(args.out).expanduser() if args.out else (
        Path.cwd() / ".co-scientist" / "runs" /
        f"{safe_slug(goal.get('title', 'run'))}-{today()}"
    )
    if out.exists() and any(out.iterdir()) and not args.force:
        # choose a unique name rather than failing the common re-run case
        i = 2
        base = out
        while out.exists() and any(out.iterdir()):
            out = base.with_name(f"{base.name}-{i}")
            i += 1
    run = Run.create(out, goal, profile=args.profile, seed=args.seed, force=args.force)
    run.touch_phase("0_intake", "initialized")
    ensure_playbook(Path(args.playbook).expanduser())
    print(json.dumps({
        "run": str(run.root),
        "run_id": run.root.name,
        "profile": args.profile,
        "budgets": PROFILES[args.profile],
        "playbook": str(Path(args.playbook).expanduser()),
        "next": [
            "python scripts/cosci.py prompt --run RUN --name supervisor "
            "--out RUN/prompts/supervisor.md --output-path RUN/inbox/supervisor.json",
            "Read the rendered prompt and execute it; write the answer to the --output-path",
            "python scripts/cosci.py prompt --run RUN --name landscape "
            "--out RUN/prompts/landscape.md --output-path RUN/inbox/landscape_main.md",
        ],
    }, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    run = Run(args.run)
    print(json.dumps(run.status(), ensure_ascii=False, indent=2))


def cmd_phase(args: argparse.Namespace) -> None:
    run = Run(args.run)
    run.touch_phase(args.phase, args.status, args.detail or "")
    print(json.dumps({
        "run": str(run.root),
        "phase": args.phase,
        "status": args.status or run.load().get("status"),
    }, ensure_ascii=False, indent=2))


def _union(a, b) -> list:
    out = []
    seen = set()
    for item in list(a or []) + list(b or []):
        key = re.sub(r"[\s。.!！;；]+$", "", str(item)).strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def cmd_apply_plan(args: argparse.Namespace) -> None:
    """Merge the supervisor plan into goal.json without overriding user constraints."""
    run = Run(args.run)
    goal = run.load_goal()
    plan = read_json_loose(Path(args.file).expanduser())
    if not isinstance(plan, dict):
        die("supervisor plan must be a JSON object")
    merged = dict(goal)
    for key in ("title", "question", "domain", "background", "decision"):
        if not merged.get(key) and plan.get(key):
            merged[key] = plan[key]
    for key in ("known", "unknown", "non_goals", "deliverables", "assumptions"):
        merged[key] = _union(merged.get(key), plan.get(key))
    merged["constraints"] = {**(plan.get("constraints") or {}), **(goal.get("constraints") or {})}
    merged["safety"] = {**(plan.get("safety") or {}), **(goal.get("safety") or {})}
    merged["preferences"] = {**(plan.get("preferences") or {}), **(goal.get("preferences") or {})}
    if not merged.get("evaluation_criteria"):
        merged["evaluation_criteria"] = plan.get("evaluation_criteria") or []
    merged["clarifying_questions"] = plan.get("clarifying_questions") or merged.get("clarifying_questions") or []
    merged["stop_conditions"] = _union(merged.get("stop_conditions"), plan.get("stop_conditions"))
    merged["supervisor_plan"] = str(Path(args.file))
    write_json(run.goal_file, merged)
    run.touch_phase("0_intake", "awaiting_confirmation", "supervisor plan merged")
    print(json.dumps({
        "run": str(run.root),
        "goal": str(run.goal_file),
        "criteria": merged.get("evaluation_criteria"),
        "clarifying_questions": merged.get("clarifying_questions"),
        "assumptions": merged.get("assumptions"),
    }, ensure_ascii=False, indent=2))


def _items_from_file(path: Path, key: str | None = None) -> list[dict]:
    path = Path(path).expanduser()
    try:
        data = read_json_loose(path)
    except json.JSONDecodeError as exc:
        die(f"invalid JSON in {path}: {exc}")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if key and isinstance(data.get(key), list):
            return data[key]
        return [data]
    die(f"expected a JSON object or array in {path}")
    return []


def cmd_ingest(args: argparse.Namespace) -> None:
    run = Run(args.run)
    items = _items_from_file(Path(args.file), args.kind)
    parents = [p for p in (args.parent or "").split(",") if p]
    if args.kind == "hypotheses":
        result = run.add_hypotheses(
            items, strategy=args.strategy, agent=args.agent,
            parent_ids=parents or None, round_no=args.round,
        )
    elif args.kind == "reviews":
        result = run.add_reviews(items, reviewer=args.reviewer or args.agent)
    elif args.kind == "matches":
        result = run.add_matches(items, judge=args.judge or args.agent)
    else:
        die(f"unknown ingest kind: {args.kind}")
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_add_hypothesis(args: argparse.Namespace) -> None:
    args.kind = "hypotheses"
    cmd_ingest(args)


def cmd_add_review(args: argparse.Namespace) -> None:
    args.kind = "reviews"
    cmd_ingest(args)


def cmd_add_match(args: argparse.Namespace) -> None:
    args.kind = "matches"
    cmd_ingest(args)


def cmd_suggest_matches(args: argparse.Namespace) -> None:
    run = Run(args.run)
    rows = run.leaderboard()
    active = [r for r in rows if r.get("status") != "rejected"]
    if len(active) < 2:
        die("need at least two active hypotheses")
    existing = {(m.get("a"), m.get("b")) for m in run.matches()}
    existing |= {(b, a) for a, b in existing}
    seed = args.seed if args.seed is not None else int(run.load().get("seed", 42))
    import random
    rng = random.Random(seed + len(existing))

    by_id = {r["id"]: r for r in active}
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_pair(a: str, b: str, reason: str, depth: str = "multi") -> None:
        if a == b:
            return
        key = tuple(sorted((a, b)))
        if key in seen:
            return
        if not args.allow_repeat and (a, b) in existing:
            return
        seen.add(key)
        order = [a, b]
        rng.shuffle(order)
        pairs.append({
            "a": a, "b": b, "order_presented": order,
            "reason": reason, "depth": depth,
        })

    top_n = min(len(active), max(8, args.n // 2))
    top = active[:top_n]
    top_ids = {r["id"] for r in top}
    lower = active[top_n:]
    while len(pairs) < args.n:
        before = len(pairs)
        if args.policy == "top" or (args.policy == "balanced" and len(pairs) < args.n * 0.5):
            for _ in range(12):
                if len(pairs) >= args.n:
                    break
                a, b = rng.sample(top, 2) if len(top) >= 2 else (None, None)
                if a and b:
                    add_pair(a["id"], b["id"], "top-vs-top", "multi")
        elif args.policy == "balanced" and len(pairs) < args.n * 0.8:
            # same-cluster matches: compare close alternatives
            clusters: dict[str, list[dict]] = {}
            for r in active:
                clusters.setdefault(r.get("cluster", "?"), []).append(r)
            multi = [v for v in clusters.values() if len(v) >= 2]
            if multi:
                group = rng.choice(multi)
                a, b = rng.sample(group, 2)
                depth = "multi" if {a["id"], b["id"]} <= top_ids else "single"
                add_pair(a["id"], b["id"], "same-cluster", depth)
        else:
            # exploration: new/lower-ranked challenger against a top hypothesis
            challengers = lower or [r for r in active if r["matches"] <= 2]
            if challengers and top:
                a = rng.choice(challengers)
                b = rng.choice(top)
                add_pair(a["id"], b["id"], "challenger-vs-top", "single")
        if len(pairs) == before:
            # fall back to any unused pair
            for _ in range(40):
                a, b = rng.sample(active, 2)
                add_pair(a["id"], b["id"], "fallback", "single")
                if len(pairs) > before:
                    break
        if len(pairs) == before:
            break

    result = {
        "run": str(run.root),
        "policy": args.policy,
        "requested": args.n,
        "matches": pairs,
        "note": "Presentation order is randomised. Judges must receive the prompt with this order and must not infer IDs from position.",
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        write_text(out, text + "\n")
        print(out)
    else:
        print(text)


def cmd_ids(args: argparse.Namespace) -> None:
    run = Run(args.run)
    rows = run.leaderboard(bootstrap_iterations=50, include_rejected=True)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f"{'ID':<10} {'STATUS':<15} {'CLUSTER':<8} {'M':>3}  TITLE")
    print("-" * 90)
    for r in rows:
        print(f"{r['id']:<10} {r['status']:<15} {r['cluster']:<8} {r['matches']:>3}  {truncate(r['title'], 60)}")


def cmd_rank(args: argparse.Namespace) -> None:
    run = Run(args.run)
    rows = run.leaderboard()
    if args.top:
        rows = rows[:args.top]
    if args.format == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=2)
    elif args.format == "csv":
        import io
        buf = io.StringIO()
        fields = ["rank", "id", "title", "elo", "elo_ci_low", "elo_ci_high", "bt_elo",
                  "matches", "wins", "losses", "draws", "review_score", "cluster", "status"]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        text = buf.getvalue()
    else:
        text = rank_text(rows)
    if args.out:
        out = Path(args.out).expanduser()
        write_text(out, text + ("" if text.endswith("\n") else "\n"))
        print(out)
    else:
        print(text)


def cmd_select(args: argparse.Namespace) -> None:
    run = Run(args.run)
    rows = run.select(top=args.top, diverse=args.diverse)
    data = {"run": str(run.root), "selected": rows}
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        write_text(out, text + "\n")
        print(out)
    else:
        print(text)


def cmd_clusters(args: argparse.Namespace) -> None:
    run = Run(args.run)
    hs = run.hypotheses(include_archived=False)
    clusters = run.recluster(args.threshold) if args.write else cluster_hypotheses(hs, args.threshold)
    dups = near_duplicates(hs, threshold=args.duplicate_threshold)
    data = {
        "run": str(run.root),
        "diversity": diversity_score(hs, args.threshold),
        "clusters": clusters,
        "near_duplicates": dups,
    }
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        write_text(out, text + "\n")
        print(out)
    else:
        print(text)


def _select_hypotheses(
    run: Run,
    spec: str | None,
    top: int | None,
    include_rejected: bool = False,
) -> list[dict]:
    hs = run.hypotheses(include_archived=False)

    def kept(h: dict) -> bool:
        return include_rejected or h.get("status") != "rejected"

    if top:
        rows = run.leaderboard(include_rejected=include_rejected)[:top]
        ids = [r["id"] for r in rows]
        return [h for h in hs if h["id"] in ids]
    if not spec or spec == "all":
        return [h for h in hs if kept(h)]
    if spec == "unreviewed":
        reviewed = {r.get("hypothesis_id") for r in run.reviews()}
        return [h for h in hs if h["id"] not in reviewed and kept(h)]
    if spec.startswith("top:"):
        n = int(spec.split(":", 1)[1])
        ids = [r["id"] for r in run.leaderboard(include_rejected=include_rejected)[:n]]
        return [h for h in hs if h["id"] in ids]
    ids = {s.strip() for s in spec.split(",") if s.strip()}
    return [h for h in hs if h["id"] in ids]


def cmd_prompt(args: argparse.Namespace) -> None:
    run = Run(args.run)
    name = args.name[:-3] if args.name.endswith(".md") else args.name
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        # Prompts are numbered for reading order (02_generation.md); accept
        # both the bare stem and the numbered filename.
        candidates = sorted(PROMPT_DIR.glob(f"*{name}.md"))
        if candidates:
            path = candidates[0]
    if not path.exists():
        available = sorted(p.stem for p in PROMPT_DIR.glob("*.md"))
        die(f"unknown prompt {name!r}; available: {', '.join(available)}")
    template = read_text(path)
    data = run.load()
    goal = read_json(run.goal_file) if run.goal_file.exists() else {}
    prompt_key = re.sub(r"^\d+_", "", name)
    # Meta-review and RSI need the rejected hypotheses too: learning from
    # failures is the point of those stages.
    include_rejected = prompt_key in {"meta_review", "rsi_retrospective"} and not args.hypotheses
    selected = _select_hypotheses(
        run, args.hypotheses, args.top, include_rejected=include_rejected
    )
    selected_ids = {h["id"] for h in selected}
    reviews = [r for r in run.reviews() if r.get("hypothesis_id") in selected_ids]
    rows = run.leaderboard(include_rejected=include_rejected)
    if args.hypotheses and not args.top:
        order = {hid: i for i, hid in enumerate(h["id"] for h in selected)}
        rows = sorted([r for r in rows if r["id"] in selected_ids],
                      key=lambda r: order.get(r["id"], 999))

    match = {}
    if args.match:
        mp = Path(args.match).expanduser()
        match = read_json_loose(mp) if mp.exists() else json.loads(args.match)
    playbook_dir = Path(args.playbook).expanduser()
    ensure_playbook(playbook_dir)
    playbook_md = read_text(playbook_dir / "PLAYBOOK.md")
    strategy_stats = read_json(playbook_dir / "strategies.json")
    schema_file = ASSET_DIR / "hypothesis.schema.json"
    schema = read_json(schema_file) if schema_file.exists() else {}

    strategy = args.strategy or ""
    agent_suffix = "json" if prompt_key in JSON_PROMPTS else "md"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    stem = f"{name}{('_' + strategy) if strategy else ''}_{stamp}"
    # The rendered prompt and the subagent's output are different files.
    # ``--out`` controls the prompt; ``--output-path`` controls the answer.
    prompt_out = Path(args.out).expanduser() if args.out else run.prompt_dir / f"{stem}.md"
    agent_out = (Path(args.output_path).expanduser() if args.output_path
                 else run.inbox_dir / f"{stem}.{agent_suffix}")
    json_out = (agent_out.with_suffix(".json") if agent_out.suffix.lower() == ".md"
                else agent_out.with_name(f"{agent_out.stem}.lessons.json"))

    outcomes_file = run.meta_dir / "outcomes.json"
    if outcomes_file.exists():
        outcomes_text = json.dumps(read_json(outcomes_file), ensure_ascii=False, indent=2)
    else:
        outcomes_text = read_text(run.meta_dir / "outcomes.md", "（无外部实验结果/专家反馈）")

    context = {
        "RUN_DIR": str(run.root),
        "RUN_ID": data.get("run_id", ""),
        "PROFILE": data.get("profile", ""),
        "DATE": now_iso(),
        "GOAL_JSON": json.dumps(goal, ensure_ascii=False, indent=2),
        "GOAL_MD": read_text(
            run.root / "goal.md",
            as_text(goal.get("raw") or goal.get("question") or ""),
        ),
        "LANDSCAPE": read_text(run.root / "landscape.md", "（尚未生成）"),
        "LEADERBOARD_MD": rank_text(rows),
        "LEADERBOARD_JSON": json.dumps(rows, ensure_ascii=False, indent=2),
        "HYPOTHESES_JSON": json.dumps(selected, ensure_ascii=False, indent=2),
        "REVIEWS_JSON": json.dumps(reviews, ensure_ascii=False, indent=2),
        "MATCH_JSON": json.dumps(match, ensure_ascii=False, indent=2),
        "PLAYBOOK_MD": playbook_md,
        "STRATEGY_STATS": json.dumps(strategy_stats, ensure_ascii=False, indent=2),
        "SCHEMA_JSON": json.dumps(schema, ensure_ascii=False, indent=2),
        "OUTPUT_PATH": str(agent_out),
        "OUTPUT_JSON_PATH": str(json_out),
        "STRATEGY": strategy,
        "TOP_N": str(args.top or 5),
        "ROUND": str(args.round or 1),
        "BUDGET_JSON": json.dumps(data.get("budgets", {}), ensure_ascii=False, indent=2),
        "RUBRIC": read_text(SKILL_ROOT / "references" / "evaluation-rubric.md", "（评分锚点文件缺失）"),
        "REVIEW_TYPE": "full",
        "META_REVIEW": read_text(run.meta_dir / "meta_review.md", "（尚未生成 meta-review）"),
        "MATCHES_JSON": json.dumps(run.matches(), ensure_ascii=False, indent=2),
        "OUTCOMES": outcomes_text,
    }
    for item in args.var or []:
        if "=" in item:
            key, value = item.split("=", 1)
            context[key] = value

    def repl(match_obj: re.Match) -> str:
        key = match_obj.group(1)
        return context.get(key, match_obj.group(0))

    rendered = re.sub(r"\{\{\s*([A-Z0-9_]+)\s*\}\}", repl, template)
    write_text(prompt_out, rendered)
    print(prompt_out)


def cmd_report(args: argparse.Namespace) -> None:
    run = Run(args.run)
    md = build_report(run, top=args.top)
    out = Path(args.out).expanduser() if args.out else run.root / "report.md"
    write_text(out, md)
    if args.json:
        data = run.load()
        payload = {
            "run": data,
            "goal": read_json(run.goal_file) if run.goal_file.exists() else {},
            "leaderboard": run.leaderboard(),
            "hypotheses": run.hypotheses(),
            "reviews": run.reviews(),
            "matches": run.matches(),
            "diversity": diversity_score([h for h in run.hypotheses(include_archived=False)
                                           if h.get("status") != "rejected"]),
            "clusters": cluster_hypotheses([h for h in run.hypotheses(include_archived=False)
                                             if h.get("status") != "rejected"]),
            "warnings": run.warnings(),
        }
        jout = (Path(args.json).expanduser() if isinstance(args.json, str)
                else run.root / "report.json")
        write_json(jout, payload)
        print(jout)
    print(out)


def cmd_outcomes(args: argparse.Namespace) -> None:
    """Validate and store structured external outcomes for a run."""
    run = Run(args.run)
    data = read_json_loose(Path(args.file).expanduser())
    items = data.get("outcomes") if isinstance(data, dict) else data
    if not isinstance(items, list):
        die("outcomes file must be a JSON array or an object with an 'outcomes' array")
    known = set(run.index().get("hypotheses", {}))
    normalized = []
    warnings = []
    for item in items:
        if isinstance(item, dict):
            hid = item.get("hypothesis_id") or item.get("id")
            raw_status = item.get("status") or item.get("outcome")
            status = normalize_outcome(raw_status)
            if raw_status and status == "unknown" and str(raw_status).strip().lower() != "unknown":
                warnings.append(
                    f"unrecognized status for {hid}: {raw_status!r}; stored as unknown"
                )
            entry = {
                "hypothesis_id": str(hid) if hid is not None else "",
                "status": status,
                "source": item.get("source", ""),
                "note": item.get("note", ""),
                "metric": item.get("metric", ""),
                "value": item.get("value"),
            }
        else:
            entry = {
                "hypothesis_id": str(item),
                "status": "unknown",
                "source": "",
                "note": "",
                "metric": "",
                "value": None,
            }
        if not entry["hypothesis_id"]:
            warnings.append("skipped an outcome without hypothesis_id")
            continue
        if entry["hypothesis_id"] not in known:
            warnings.append(f"skipped unknown hypothesis: {entry['hypothesis_id']}")
            continue
        normalized.append(entry)
    out = (Path(args.out).expanduser() if args.out
           else run.meta_dir / "outcomes.json")
    write_json(out, {"schema_version": 1, "outcomes": normalized})
    print(json.dumps({
        "run": str(run.root),
        "out": str(out),
        "n": len(normalized),
        "status_counts": {
            status: sum(1 for e in normalized if e["status"] == status)
            for status in ("supported", "refuted", "inconclusive", "unknown")
        },
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))


def cmd_rsi_compare(args: argparse.Namespace) -> None:
    run_a = Run(args.run_a)
    run_b = Run(args.run_b)
    data = compare_runs(run_a, run_b, top=args.top)
    if args.format == "json":
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = compare_text(data)
    if args.out:
        out = Path(args.out).expanduser()
        write_text(out, text + ("" if text.endswith("\n") else "\n"))
        print(out)
    else:
        print(text)


def cmd_rsi(args: argparse.Namespace) -> None:
    playbook = Path(args.playbook).expanduser()
    if args.rsi_command == "status":
        print(json.dumps(rsi_status(playbook), ensure_ascii=False, indent=2))
    elif args.rsi_command == "suggest":
        result = suggest_strategies(
            playbook, n=args.n, exploration=args.exploration,
            seed=args.seed, pool=args.pool,
        )
        print(json.dumps({
            "playbook": str(playbook),
            **result,
        }, ensure_ascii=False, indent=2))
    elif args.rsi_command == "update":
        run = Run(args.run)
        result = update_from_run(
            run, playbook, apply=args.apply,
            approve=args.approve, decay=args.decay,
            seed=args.seed, min_samples=args.min_samples,
            min_external_samples=args.min_external_samples,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.rsi_command in ("compare", "ab"):
        cmd_rsi_compare(args)
    elif args.rsi_command == "rollback":
        print(json.dumps(rollback(playbook, args.to_version),
                         ensure_ascii=False, indent=2))
    else:
        die(f"unknown rsi command: {args.rsi_command}")


def cmd_validate(args: argparse.Namespace) -> None:
    run = Run(args.run)
    errors: list[str] = []
    warnings: list[str] = []
    if not run.run_file.exists():
        errors.append("missing run.json")
    if not run.goal_file.exists():
        errors.append("missing goal.json")
    index = run.index()
    hs = run.hypotheses()
    ids = {h["id"] for h in hs}
    for hid, entry in index.get("hypotheses", {}).items():
        if not (run.root / entry.get("file", f"hypotheses/{hid}.json")).exists():
            errors.append(f"{hid}: hypothesis file missing")
    fps: dict[str, str] = {}
    for h in hs:
        if not h.get("title"):
            errors.append(f"{h['id']}: missing title")
        if not h.get("claim"):
            errors.append(f"{h['id']}: missing claim")
        if not h.get("predictions"):
            warnings.append(f"{h['id']}: no falsifiable predictions")
        if not (h.get("testability") or {}).get("minimal_experiment"):
            warnings.append(f"{h['id']}: no minimal experiment")
        for e in h.get("evidence") or []:
            if isinstance(e, dict) and not e.get("verified"):
                warnings.append(f"{h['id']}: unverified citation {e.get('doi') or e.get('source')}")
        fp = index.get("hypotheses", {}).get(h["id"], {}).get("fingerprint")
        if fp:
            if fp in fps:
                errors.append(f"duplicate fingerprint: {h['id']} == {fps[fp]}")
            fps[fp] = h["id"]
    for r in run.reviews():
        hid = r.get("hypothesis_id")
        if hid not in ids:
            errors.append(f"review references unknown hypothesis {hid}")
        for k, v in (r.get("scores") or {}).items():
            try:
                value = float(v)
            except (TypeError, ValueError):
                errors.append(f"review {hid}: score {k}={v!r} is not numeric")
                continue
            if not (0 <= value <= 10):
                errors.append(f"review {hid}: score {k}={v} outside 0-10")
    for m in run.matches():
        if m.get("a") not in ids or m.get("b") not in ids:
            errors.append(f"match {m.get('id')} references unknown hypothesis")
        if m.get("winner") not in (m.get("a"), m.get("b"), "draw"):
            errors.append(f"match {m.get('id')} has invalid winner")
        order = m.get("order_presented") or []
        if order and set(order) != {m.get("a"), m.get("b")}:
            errors.append(f"match {m.get('id')} has invalid order_presented")
    goal = run.load_goal()
    criteria = goal.get("evaluation_criteria") or []
    weights = []
    for criterion in criteria:
        if isinstance(criterion, dict):
            try:
                weights.append(float(criterion.get("weight", 0)))
            except (TypeError, ValueError):
                errors.append(f"evaluation_criteria weight is not numeric: {criterion!r}")
    if weights and abs(sum(weights) - 1.0) > 0.01:
        warnings.append(f"evaluation_criteria weights sum to {sum(weights):.3f}, not 1.0")

    rows = run.leaderboard(bootstrap_iterations=100, include_rejected=True)
    top5 = rows[:5]
    for r in top5:
        if r.get("status") == "rejected":
            warnings.append(f"top-5 {r['id']} is rejected; restore or exclude it")
    if len(run.matches()) < max(4, len(hs)):
        warnings.append("match count is low relative to hypotheses; ranking is unstable")
    if not (run.root / "report.md").exists():
        warnings.append("report.md not generated yet")
    result = {
        "run": str(run.root),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "hypotheses": len(hs),
            "reviews": len(run.reviews()),
            "matches": len(run.matches()),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


def cmd_export(args: argparse.Namespace) -> None:
    run = Run(args.run)
    payload = {
        "run": run.load(),
        "goal": read_json(run.goal_file) if run.goal_file.exists() else {},
        "leaderboard": run.leaderboard(),
        "hypotheses": run.hypotheses(),
        "reviews": run.reviews(),
        "matches": run.matches(),
        "diversity": diversity_score([h for h in run.hypotheses(include_archived=False)
                                       if h.get("status") != "rejected"]),
    }
    out = Path(args.out).expanduser() if args.out else run.root / "export.json"
    write_json(out, payload)
    print(out)


def cmd_list_runs(args: argparse.Namespace) -> None:
    root = Path(args.dir)
    runs = []
    for p in sorted(root.rglob("run.json")):
        try:
            data = read_json(p)
            runs.append({
                "run_id": data.get("run_id"),
                "path": str(p.parent),
                "profile": data.get("profile"),
                "phase": data.get("phase"),
                "status": data.get("status"),
                "created_at": data.get("created_at"),
                "counters": data.get("counters"),
            })
        except Exception:
            continue
    print(json.dumps(runs, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cosci",
        description="Co-Scientist + RSI research workflow: hypotheses, reviews, tournaments, reports.",
    )
    p.add_argument("--version", action="version", version="co-scientist 1.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a run from a goal file")
    init.add_argument("--goal", required=True, help="goal .yaml/.json/.md/.txt")
    init.add_argument("--out", help="run directory")
    init.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    init.add_argument("--seed", type=int, default=42)
    init.add_argument("--title")
    init.add_argument("--raw-text", help="original brief, stored verbatim")
    init.add_argument("--playbook", default=str(DEFAULT_PLAYBOOK_DIR))
    init.add_argument("--force", action="store_true", help="delete an existing non-empty run dir")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="print run status")
    status.add_argument("--run", required=True)
    status.set_defaults(func=cmd_status)

    phase = sub.add_parser("phase", help="record a phase transition")
    phase.add_argument("--run", required=True)
    phase.add_argument("--phase", required=True,
                       help="0_intake|1_landscape|2_generation|3_reflection|4_tournament|5_evolution|6_meta_review|7_synthesis|8_rsi|done")
    phase.add_argument("--status", choices=["initialized", "awaiting_confirmation", "running", "paused", "done", "failed"])
    phase.add_argument("--detail")
    phase.set_defaults(func=cmd_phase)

    plan = sub.add_parser("apply-plan", help="merge supervisor JSON into goal.json")
    plan.add_argument("--run", required=True)
    plan.add_argument("--file", required=True)
    plan.set_defaults(func=cmd_apply_plan)

    ingest = sub.add_parser("ingest", help="ingest a JSON file of hypotheses/reviews/matches")
    ingest.add_argument("--run", required=True)
    ingest.add_argument("--kind", choices=["hypotheses", "reviews", "matches"], required=True)
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--strategy")
    ingest.add_argument("--agent")
    ingest.add_argument("--parent", help="comma-separated parent hypothesis IDs")
    ingest.add_argument("--round", type=int)
    ingest.add_argument("--reviewer")
    ingest.add_argument("--judge")
    ingest.set_defaults(func=cmd_ingest)

    for name, fn in (("add-hypothesis", cmd_add_hypothesis),
                     ("add-review", cmd_add_review),
                     ("add-match", cmd_add_match)):
        sp = sub.add_parser(name, help=f"add a {name.split('-')[1]} from JSON")
        sp.add_argument("--run", required=True)
        sp.add_argument("--file", required=True)
        sp.add_argument("--strategy")
        sp.add_argument("--agent")
        sp.add_argument("--parent")
        sp.add_argument("--round", type=int)
        sp.add_argument("--reviewer")
        sp.add_argument("--judge")
        sp.set_defaults(func=fn)

    sm = sub.add_parser("suggest-matches", help="schedule the next tournament matches")
    sm.add_argument("--run", required=True)
    sm.add_argument("--n", type=int, default=10)
    sm.add_argument("--policy", choices=["balanced", "top", "exploration"], default="balanced")
    sm.add_argument("--allow-repeat", action="store_true")
    sm.add_argument("--seed", type=int)
    sm.add_argument("--out")
    sm.set_defaults(func=cmd_suggest_matches)

    ids = sub.add_parser("ids", help="list hypothesis IDs and status")
    ids.add_argument("--run", required=True)
    ids.add_argument("--format", choices=["table", "json"], default="table")
    ids.set_defaults(func=cmd_ids)

    rank = sub.add_parser("rank", help="show Elo / Bradley-Terry leaderboard")
    rank.add_argument("--run", required=True)
    rank.add_argument("--top", type=int)
    rank.add_argument("--format", choices=["table", "json", "csv"], default="table")
    rank.add_argument("--out")
    rank.set_defaults(func=cmd_rank)

    sel = sub.add_parser("select", help="select top + diverse hypotheses for evolution")
    sel.add_argument("--run", required=True)
    sel.add_argument("--top", type=int, default=5)
    sel.add_argument("--diverse", type=int, default=2)
    sel.add_argument("--out")
    sel.set_defaults(func=cmd_select)

    cl = sub.add_parser("clusters", help="proximity clusters and near-duplicates")
    cl.add_argument("--run", required=True)
    cl.add_argument("--threshold", type=float, default=0.55)
    cl.add_argument("--duplicate-threshold", type=float, default=0.85)
    cl.add_argument("--write", action="store_true", help="persist cluster labels")
    cl.add_argument("--out")
    cl.set_defaults(func=cmd_clusters)

    pr = sub.add_parser("prompt", help="render an agent prompt with run context")
    pr.add_argument("--run", required=True)
    pr.add_argument("--name", required=True, help="prompt stem, e.g. generation")
    pr.add_argument("--strategy")
    pr.add_argument("--hypotheses", help="all | top:N | unreviewed | H001,H002")
    pr.add_argument("--match", help="match JSON or path")
    pr.add_argument("--top", type=int)
    pr.add_argument("--round", type=int)
    pr.add_argument("--playbook", default=str(DEFAULT_PLAYBOOK_DIR))
    pr.add_argument("--out", help="where to write the rendered prompt")
    pr.add_argument("--output-path",
                    help="where the subagent must write its answer (default: RUN/inbox/...)")
    pr.add_argument("--var", action="append", help="extra KEY=VALUE substitution")
    pr.set_defaults(func=cmd_prompt)

    rep = sub.add_parser("report", help="assemble report.md from run artefacts")
    rep.add_argument("--run", required=True)
    rep.add_argument("--top", type=int, default=5)
    rep.add_argument("--out")
    rep.add_argument("--json", nargs="?", const=True, default=False,
                     help="also write report.json (optional path)")
    rep.set_defaults(func=cmd_report)

    rsi = sub.add_parser("rsi", help="Recursive Self-Improvement playbook")
    rsi.add_argument("--playbook", default=str(DEFAULT_PLAYBOOK_DIR))
    rsi_sub = rsi.add_subparsers(dest="rsi_command", required=True)
    rs = rsi_sub.add_parser("status", help="show playbook status and posteriors")
    rs.add_argument("--playbook", default=argparse.SUPPRESS,
                    help="override the parent --playbook")
    rs.set_defaults(func=cmd_rsi)
    rsg = rsi_sub.add_parser("suggest", help="sample strategies from one pool")
    rsg.add_argument("--n", type=int, default=4)
    rsg.add_argument("--exploration", type=float, default=0.2)
    rsg.add_argument("--seed", type=int)
    rsg.add_argument("--pool", choices=["generation", "evolution", "all"],
                     default="generation",
                     help="which strategy pool to sample from (default: generation)")
    rsg.add_argument("--playbook", default=argparse.SUPPRESS,
                     help="override the parent --playbook")
    rsg.set_defaults(func=cmd_rsi)
    ru = rsi_sub.add_parser("update", help="update posteriors from a finished run")
    ru.add_argument("--run", required=True)
    ru.add_argument("--apply", action="store_true")
    ru.add_argument("--approve", action="store_true")
    ru.add_argument("--decay", type=float, default=0.98)
    ru.add_argument("--min-samples", type=int, default=3,
                    help="minimum Elo-based observations per strategy before updating")
    ru.add_argument("--min-external-samples", type=int, default=1,
                    help="minimum external outcomes per strategy before updating")
    ru.add_argument("--seed", type=int, default=42)
    ru.add_argument("--playbook", default=argparse.SUPPRESS,
                    help="override the parent --playbook")
    ru.set_defaults(func=cmd_rsi)
    rr = rsi_sub.add_parser("rollback", help="restore a playbook snapshot")
    rr.add_argument("--to-version", type=int, required=True)
    rr.add_argument("--playbook", default=argparse.SUPPRESS,
                    help="override the parent --playbook")
    rr.set_defaults(func=cmd_rsi)
    for name, help_text in (
        ("compare", "compare two completed runs (observational, not a controlled A/B)"),
        ("ab", "alias for compare"),
    ):
        rc = rsi_sub.add_parser(name, help=help_text)
        rc.add_argument("--run-a", required=True)
        rc.add_argument("--run-b", required=True)
        rc.add_argument("--top", type=int, default=5)
        rc.add_argument("--format", choices=["text", "json"], default="text")
        rc.add_argument("--out")
        rc.set_defaults(func=cmd_rsi, rsi_command=name)

    outs = sub.add_parser("outcomes", help="validate and store external outcomes")
    outs.add_argument("--run", required=True)
    outs.add_argument("--file", required=True)
    outs.add_argument("--out")
    outs.set_defaults(func=cmd_outcomes)

    val = sub.add_parser("validate", help="check run integrity")
    val.add_argument("--run", required=True)
    val.set_defaults(func=cmd_validate)

    exp = sub.add_parser("export", help="export the whole run as one JSON")
    exp.add_argument("--run", required=True)
    exp.add_argument("--out")
    exp.set_defaults(func=cmd_export)

    lr = sub.add_parser("list-runs", help="list runs under a directory")
    lr.add_argument("--dir", default=str(Path.cwd() / ".co-scientist" / "runs"))
    lr.set_defaults(func=cmd_list_runs)

    return p


def main(argv: list[str] | None = None) -> None:
    force_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RunError as exc:
        die(str(exc))
    except FileNotFoundError as exc:
        die(f"file not found: {exc.filename or exc}")
    except json.JSONDecodeError as exc:
        die(f"invalid JSON: {exc}")

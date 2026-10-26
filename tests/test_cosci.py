#!/usr/bin/env python3
"""End-to-end tests for the Co-Scientist skill.  Uses only stdlib.

Run:  python tests/test_cosci.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "cosci.py"
TMP_ROOT = ROOT / "tests" / "tmp"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class CoScientistTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        cls.dir = Path(tempfile.mkdtemp(prefix="cosci-", dir=str(TMP_ROOT)))
        cls.run_dir = cls.dir / "run"
        cls.playbook = cls.dir / "playbook"
        cls.goal = {
            "title": "测试：河岸带氮去除",
            "question": "哪条机制最可能解释反硝化与厌氧氨氧化的耦合？",
            "background": "合成测试目标，仅验证工作流。",
            "known": ["反硝化通常是主导路径"],
            "unknown": ["田间尺度耦合机制"],
            "constraints": {"time": "8 weeks", "people": "1 student"},
            "evaluation_criteria": [
                {"name": "novelty", "weight": 0.3},
                {"name": "testability", "weight": 0.3},
                {"name": "feasibility", "weight": 0.4},
            ],
            "safety": {"data_sensitivity": "public"},
        }
        cls.goal_file = cls.dir / "goal.json"
        write_json(cls.goal_file, cls.goal)
        run("init", "--goal", str(cls.goal_file), "--out", str(cls.run_dir),
            "--profile", "quick", "--playbook", str(cls.playbook))

        cls.hypotheses = [
            {
                "title": "界面耦合",
                "claim": "DO 波动在毫米尺度形成反硝化-厌氧氨氧化耦合热点。",
                "mechanism": ["DO 波动", "微区缺氧", "耦合"],
                "predictions": [{"if_true": "DO 波动处 anammox 贡献升高",
                                 "if_false": "无空间相关"}],
                "testability": {"minimal_experiment": "原位微电极 + 15N 培养",
                                "time": "4 weeks"},
                "novelty": {"score": 7, "closest_prior_work": "E1", "delta": "毫米尺度"},
                "keywords": ["DO", "anammox", "hotspot"],
                "evidence": [{"source": "E1", "doi": "10.1000/test1", "verified": False}],
                "provenance": {"strategy": "literature_gap"},
            },
            {
                "title": "碳氮计量",
                "claim": "C/N 阈值控制两条路径的相对贡献。",
                "mechanism": ["C/N", "电子供体", "路径竞争"],
                "predictions": [{"if_true": "C/N 低于阈值时 anammox 占比升高"}],
                "testability": {"minimal_experiment": "梯度培养", "time": "3 weeks"},
                "novelty": {"score": 4, "closest_prior_work": "E2", "delta": "阈值"},
                "keywords": ["C/N", "competition"],
                "provenance": {"strategy": "data_driven"},
            },
            {
                "title": "抗生素遗留",
                "claim": "抗生素暴露后恢复顺序由功能基因拷贝数决定。",
                "mechanism": ["抗生素", "nosZ/hzsB", "恢复"],
                "predictions": [{"if_true": "nosZ 先恢复", "if_false": "同步恢复"}],
                "testability": {"minimal_experiment": "qPCR 时间序列", "time": "6 weeks"},
                "novelty": {"score": 8, "closest_prior_work": "E3", "delta": "恢复顺序"},
                "keywords": ["antibiotic", "nosZ", "hzsB"],
                "provenance": {"strategy": "assumption_decomposition"},
            },
            {
                "title": "同位素轨迹",
                "claim": "Δδ18O:Δδ15N 斜率可区分反硝化与亚硝酸盐氧化。",
                "mechanism": ["同位素分馏", "混合", "轨迹"],
                "predictions": [{"if_true": "斜率落在 1.0-2.0"}],
                "testability": {"minimal_experiment": "同位素混合模型", "time": "2 weeks"},
                "novelty": {"score": 6, "closest_prior_work": "E4", "delta": "联合诊断"},
                "keywords": ["isotope", "fractionation"],
                "provenance": {"strategy": "cross_domain_analogy"},
            },
        ]
        cls.hs_file = cls.dir / "hs.json"
        write_json(cls.hs_file, cls.hypotheses)
        cls.ingest = json.loads(run(
            "ingest", "--run", str(cls.run_dir), "--kind", "hypotheses",
            "--file", str(cls.hs_file), "--agent", "test", "--round", "1",
        ).stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_01_init_and_ingest(self):
        self.assertEqual(self.ingest["total"], 4)
        self.assertEqual(len(self.ingest["added"]), 4)
        p = self.run_dir / "hypotheses" / "H001.json"
        h = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(len(h["predictions"]), 1)
        self.assertEqual(len(h["evidence"]), 1)
        self.assertEqual(h["provenance"]["strategy"], "literature_gap")

    def test_02_duplicate_fingerprint_skipped(self):
        f = self.dir / "dup.json"
        write_json(f, [self.hypotheses[0]])
        out = json.loads(run("ingest", "--run", str(self.run_dir),
                             "--kind", "hypotheses", "--file", str(f)).stdout)
        self.assertEqual(len(out["added"]), 0)
        self.assertIn("duplicate", out["skipped"][0]["reason"])

    def test_03_reviews(self):
        reviews = [
            {"hypothesis_id": "H001", "review_type": "full", "verdict": "pass",
             "scores": {"correctness": 7, "novelty": 7, "testability": 8,
                        "impact": 7, "feasibility": 6, "grounding": 7},
             "fatal_flaws": [], "confidence": 0.7},
            {"hypothesis_id": "H002", "review_type": "full", "verdict": "revise",
             "scores": {"correctness": 6, "novelty": 4, "testability": 8,
                        "impact": 5, "feasibility": 8, "grounding": 6},
             "fatal_flaws": ["阈值可能已发表"], "confidence": 0.6},
            {"hypothesis_id": "H003", "review_type": "deep", "verdict": "pass",
             "scores": {"correctness": 7, "novelty": 8, "testability": 7,
                        "impact": 7, "feasibility": 6, "grounding": 7},
             "fatal_flaws": [], "confidence": 0.65},
            {"hypothesis_id": "H004", "review_type": "full", "verdict": "pass",
             "scores": {"correctness": 6, "novelty": 6, "testability": 9,
                        "impact": 6, "feasibility": 8, "grounding": 6},
             "fatal_flaws": [], "confidence": 0.7},
        ]
        f = self.dir / "reviews.json"
        write_json(f, reviews)
        out = json.loads(run("ingest", "--run", str(self.run_dir), "--kind", "reviews",
                             "--file", str(f), "--reviewer", "test").stdout)
        self.assertEqual(len(out["added"]), 4)
        h2 = json.loads((self.run_dir / "hypotheses" / "H002.json").read_text(encoding="utf-8"))
        self.assertEqual(h2["status"], "needs_revision")

    def test_04_matches_and_elo(self):
        matches = [
            {"a": "H001", "b": "H003", "winner": "H001",
             "order_presented": ["H003", "H001"], "confidence": 0.7},
            {"a": "H003", "b": "H001", "winner": "H001",
             "order_presented": ["H001", "H003"], "confidence": 0.8},
            {"a": "H002", "b": "H004", "winner": "H004",
             "order_presented": ["H002", "H004"], "confidence": 0.6},
            {"a": "H004", "b": "H001", "winner": "draw",
             "order_presented": ["H004", "H001"], "confidence": 0.5},
            {"a": "H002", "b": "H003", "winner": "H003",
             "order_presented": ["H003", "H002"], "confidence": 0.6},
        ]
        f = self.dir / "matches.json"
        write_json(f, matches)
        out = json.loads(run("ingest", "--run", str(self.run_dir), "--kind", "matches",
                             "--file", str(f), "--judge", "test").stdout)
        self.assertEqual(len(out["added"]), 5)
        ranked = json.loads(run("rank", "--run", str(self.run_dir),
                                "--format", "json").stdout)
        self.assertEqual(len(ranked), 4)
        self.assertEqual(ranked[0]["id"], "H001")
        self.assertGreater(ranked[0]["elo"], 1200)
        self.assertIsNotNone(ranked[0]["elo_ci_low"])
        ids_out = run("ids", "--run", str(self.run_dir)).stdout
        self.assertIn("H001", ids_out)
        self.assertIn("H004", ids_out)

    def test_05_clusters(self):
        out = json.loads(run("clusters", "--run", str(self.run_dir)).stdout)
        self.assertIn("diversity", out)
        self.assertGreaterEqual(out["diversity"]["clusters"], 1)

    def test_06_prompt_render(self):
        out = run("prompt", "--run", str(self.run_dir), "--name", "generation",
                  "--strategy", "literature_gap", "--round", "2",
                  "--out", str(self.dir / "gen.md")).stdout.strip()
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("literature_gap", text)
        self.assertIn("测试：河岸带氮去除", text)
        self.assertNotIn("{{GOAL_JSON}}", text)
        self.assertNotIn("{{STRATEGY}}", text)

    def test_07_report_and_validate(self):
        run("report", "--run", str(self.run_dir), "--top", "3",
            "--out", str(self.dir / "report.md"), "--json", str(self.dir / "report.json"))
        md = (self.dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("锦标赛排行榜", md)
        self.assertIn("H001", md)
        self.assertTrue((self.dir / "report.json").exists())
        out = json.loads(run("validate", "--run", str(self.run_dir)).stdout)
        self.assertTrue(out["ok"])

    def test_08_rsi_update(self):
        retro = {
            "run_id": "test",
            "lessons": [
                {"text": "out_of_box 需要 feasibility 预筛", "scope": "global",
                 "confidence": 0.6, "evidence": ["H003"]}
            ],
            "proposed_edits": [],
        }
        write_json(self.run_dir / "meta" / "retrospective.json", retro)
        dry = json.loads(run("rsi", "update", "--run", str(self.run_dir),
                             "--playbook", str(self.playbook)).stdout)
        self.assertFalse(dry["updated"])
        applied = json.loads(run("rsi", "update", "--run", str(self.run_dir),
                                 "--playbook", str(self.playbook),
                                 "--apply", "--approve").stdout)
        self.assertTrue(applied["updated"])
        playbook_md = (self.playbook / "PLAYBOOK.md").read_text(encoding="utf-8")
        self.assertIn("RSI Playbook", playbook_md)
        self.assertIn("out_of_box 需要 feasibility 预筛", playbook_md)

    def test_09_suggest_matches(self):
        target = self.dir / "m.json"
        run("suggest-matches", "--run", str(self.run_dir),
            "--n", "3", "--allow-repeat", "--out", str(target))
        out = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(out["matches"]), 3)
        for m in out["matches"]:
            self.assertEqual(set(m["order_presented"]), {m["a"], m["b"]})
            self.assertIn(m["depth"], {"multi", "single"})
            if m["reason"] == "top-vs-top":
                self.assertEqual(m["depth"], "multi")

    def test_10_apply_plan_and_phase(self):
        plan = {
            "title": "Supervisor title",
            "question": "Supervisor question",
            "evaluation_criteria": [
                {"name": "novelty", "weight": 0.5, "rubric": "plan rubric"},
                {"name": "feasibility", "weight": 0.5, "rubric": "plan rubric"},
            ],
            "constraints": {"time": "should not override user", "new": "value"},
            "clarifying_questions": ["Q1?"],
            "assumptions": ["A1"],
        }
        f = self.dir / "plan.json"
        write_json(f, plan)
        run("apply-plan", "--run", str(self.run_dir), "--file", str(f))
        goal = json.loads((self.run_dir / "goal.json").read_text(encoding="utf-8"))
        self.assertEqual(goal["constraints"]["time"], "8 weeks")  # user wins
        self.assertEqual(goal["constraints"]["new"], "value")
        self.assertEqual(goal["clarifying_questions"], ["Q1?"])
        self.assertEqual(goal["evaluation_criteria"][0]["name"], "novelty")

        run("phase", "--run", str(self.run_dir), "--phase", "4_tournament",
            "--status", "running")
        data = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(data["phase"], "4_tournament")
        self.assertEqual(data["status"], "running")

    def test_11_prompt_paths_are_separate(self):
        prompt_file = self.dir / "prompt_paths.md"
        answer_file = self.dir / "answer_paths.json"
        run("prompt", "--run", str(self.run_dir), "--name", "generation",
            "--strategy", "literature_gap",
            "--out", str(prompt_file), "--output-path", str(answer_file))
        text = prompt_file.read_text(encoding="utf-8")
        # The rendered prompt must point the subagent at the answer file,
        # never at the prompt file it is currently reading.
        self.assertIn(str(answer_file), text)
        self.assertNotIn(str(prompt_file), text)
        self.assertNotIn("{{OUTPUT_PATH}}", text)

    def test_12_match_without_ab_is_recovered(self):
        matches = [{
            "id": "M9001",
            "order_presented": ["H002", "H004"],
            "winner": "H004",
            "confidence": 0.6,
        }]
        f = self.dir / "match_no_ab.json"
        write_json(f, matches)
        out = json.loads(run("ingest", "--run", str(self.run_dir),
                             "--kind", "matches", "--file", str(f)).stdout)
        self.assertEqual(len(out["added"]), 1)
        tournament = json.loads(
            (self.run_dir / "tournament.json").read_text(encoding="utf-8")
        )
        match = next(m for m in tournament["matches"] if m["id"] == "M9001")
        self.assertEqual({match["a"], match["b"]}, {"H002", "H004"})
        self.assertEqual(match["winner"], "H004")

    def test_13_review_score_is_weighted(self):
        rows = json.loads(run("rank", "--run", str(self.run_dir),
                              "--format", "json").stdout)
        h1 = next(r for r in rows if r["id"] == "H001")
        # goal weights: novelty .3, testability .3, feasibility .4
        # H001 scores: novelty 7, testability 8, feasibility 6
        self.assertAlmostEqual(h1["review_score"], 6.9, places=2)
        self.assertAlmostEqual(h1["review_mean"], 7.0, places=2)

    def test_14_report_reads_retrospective_json(self):
        retro = {
            "run_id": "test",
            "lessons": [{
                "text": "报告应该读取 retrospective.json",
                "scope": "global",
                "confidence": 0.6,
                "evidence": ["H001"],
            }],
            "proposed_edits": [],
        }
        write_json(self.run_dir / "meta" / "retrospective.json", retro)
        out = self.dir / "report_retro.md"
        run("report", "--run", str(self.run_dir), "--top", "2", "--out", str(out))
        md = out.read_text(encoding="utf-8")
        self.assertIn("报告应该读取 retrospective.json", md)

    def test_15_rsi_lessons_require_approval(self):
        playbook = self.dir / "playbook_noapprove"
        retro = {
            "run_id": "test",
            "lessons": [{
                "text": "未经批准不应写入 lessons",
                "scope": "global",
                "confidence": 0.5,
                "evidence": ["H001"],
            }],
            "proposed_edits": [],
        }
        write_json(self.run_dir / "meta" / "retrospective.json", retro)
        run("rsi", "update", "--run", str(self.run_dir),
            "--playbook", str(playbook), "--apply")
        lessons = playbook / "lessons.jsonl"
        self.assertEqual(lessons.read_text(encoding="utf-8").strip(), "")
        # With approval the lesson is appended.
        run("rsi", "update", "--run", str(self.run_dir),
            "--playbook", str(playbook), "--apply", "--approve")
        self.assertIn("未经批准不应写入 lessons",
                      lessons.read_text(encoding="utf-8"))

    def test_16_meta_review_and_rsi_see_rejected(self):
        new_h = {
            "title": "应被淘汰的测试假设",
            "claim": "这是一个用于验证淘汰假设可见性的合成陈述。",
            "mechanism": ["A", "B"],
            "predictions": [{"if_true": "X", "if_false": "Y"}],
            "testability": {"minimal_experiment": "合成实验"},
            "provenance": {"strategy": "literature_gap"},
        }
        f = self.dir / "rejected_h.json"
        write_json(f, [new_h])
        ingested = json.loads(run("ingest", "--run", str(self.run_dir),
                                  "--kind", "hypotheses", "--file", str(f)).stdout)
        hid = ingested["added"][0]["id"]
        review = [{
            "hypothesis_id": hid,
            "review_type": "full",
            "verdict": "reject",
            "scores": {"correctness": 2, "novelty": 2, "testability": 2,
                       "impact": 2, "feasibility": 2, "grounding": 2},
            "fatal_flaws": ["合成测试：故意淘汰"],
            "confidence": 0.8,
        }]
        rf = self.dir / "rejected_review.json"
        write_json(rf, review)
        run("ingest", "--run", str(self.run_dir), "--kind", "reviews",
            "--file", str(rf), "--reviewer", "test")

        meta_prompt = self.dir / "meta_review_prompt.md"
        run("prompt", "--run", str(self.run_dir), "--name", "meta_review",
            "--out", str(meta_prompt), "--output-path", str(self.dir / "mr.md"))
        self.assertIn(hid, meta_prompt.read_text(encoding="utf-8"))

        rsi_prompt = self.dir / "rsi_prompt.md"
        run("prompt", "--run", str(self.run_dir), "--name", "rsi_retrospective",
            "--out", str(rsi_prompt), "--output-path", str(self.dir / "retro.json"))
        self.assertIn(hid, rsi_prompt.read_text(encoding="utf-8"))

    def test_17_rsi_caps_proposed_edits(self):
        retro = {
            "run_id": "test",
            "lessons": [],
            "proposed_edits": [
                {"op": "update", "target": f"strategy:s{i}", "change": "x"}
                for i in range(5)
            ],
        }
        write_json(self.run_dir / "meta" / "retrospective.json", retro)
        out = json.loads(run("rsi", "update", "--run", str(self.run_dir),
                             "--playbook", str(self.dir / "playbook_cap")).stdout)
        self.assertEqual(len(out["proposed_edits"]), 3)
        self.assertTrue(out["record"]["guardrail_warnings"])

    def test_18_rsi_counts_rejected_as_failures(self):
        write_json(self.run_dir / "meta" / "retrospective.json",
                   {"lessons": [], "proposed_edits": []})
        out = json.loads(run(
            "rsi", "update", "--run", str(self.run_dir),
            "--playbook", str(self.dir / "playbook_stats"),
            "--min-samples", "1",
        ).stdout)
        changes = {c["strategy"]: c for c in out["changes"]}
        lit = changes["literature_gap"]
        # H001 survived and is top-quartile; H005 was rejected.  The rejected
        # hypothesis must be a failure, not silently disappear.
        self.assertEqual(lit["n_attempted"], 2)
        self.assertEqual(lit["n_evaluated"], 1)
        self.assertEqual(lit["n_rejected"], 1)
        self.assertEqual(lit["successes"] + lit["failures"], 2)
        self.assertGreaterEqual(lit["failures"], 1)  # the rejected H005
        self.assertEqual(lit["evidence"], "elo")
        self.assertTrue(lit["updated"])

    def test_19_external_outcomes_override_elo(self):
        outcomes = {
            "outcomes": [
                {"hypothesis_id": "H001", "status": "supported", "source": "pilot"},
                {"hypothesis_id": "H002", "status": "refuted", "source": "qPCR"},
                {"hypothesis_id": "H003", "status": "supported", "source": "expert"},
                {"hypothesis_id": "H004", "status": "refuted", "source": "pilot"},
            ]
        }
        write_json(self.run_dir / "meta" / "outcomes.json", outcomes)
        out = json.loads(run(
            "rsi", "update", "--run", str(self.run_dir),
            "--playbook", str(self.dir / "playbook_ext"),
            "--min-samples", "99", "--min-external-samples", "1",
        ).stdout)
        changes = {c["strategy"]: c for c in out["changes"]}
        lit = changes["literature_gap"]
        self.assertEqual(lit["evidence"], "external")
        self.assertEqual(lit["n_external"], 1)
        self.assertEqual(lit["successes"], 1)
        self.assertEqual(lit["failures"], 1)
        self.assertTrue(lit["updated"])
        data = changes["data_driven"]
        self.assertEqual(data["evidence"], "external")
        self.assertEqual(data["successes"], 0)
        self.assertEqual(data["failures"], 1)
        self.assertTrue(data["updated"])
        assump = changes["assumption_decomposition"]
        self.assertEqual(assump["evidence"], "external")
        self.assertEqual(assump["successes"], 1)
        self.assertEqual(assump["failures"], 0)

    def test_20_rsi_suggest_filters_pool(self):
        gen = json.loads(run(
            "rsi", "suggest", "--playbook", str(self.dir / "playbook_pool"),
            "--pool", "generation", "--n", "20", "--seed", "1",
        ).stdout)
        self.assertEqual(gen["pool"], "generation")
        self.assertTrue(gen["suggestions"])
        for item in gen["suggestions"]:
            self.assertIn("generation", item["pools"])
        self.assertNotIn("assumption_repair",
                         {item["strategy"] for item in gen["suggestions"]})

        evo = json.loads(run(
            "rsi", "suggest", "--playbook", str(self.dir / "playbook_pool"),
            "--pool", "evolution", "--n", "20", "--seed", "1",
        ).stdout)
        self.assertEqual(evo["pool"], "evolution")
        self.assertTrue(evo["suggestions"])
        for item in evo["suggestions"]:
            self.assertIn("evolution", item["pools"])
        self.assertIn("assumption_repair",
                      {item["strategy"] for item in evo["suggestions"]})

    def test_21_rsi_compare(self):
        out = json.loads(run(
            "rsi", "compare", "--run-a", str(self.run_dir),
            "--run-b", str(self.run_dir), "--top", "3", "--format", "json",
        ).stdout)
        self.assertIn("run_a", out)
        self.assertIn("run_b", out)
        self.assertIn("warnings", out)
        self.assertEqual(out["top_k"], 3)
        self.assertGreaterEqual(out["top_title_overlap"], 0.0)
        alias = json.loads(run(
            "rsi", "ab", "--run-a", str(self.run_dir),
            "--run-b", str(self.run_dir), "--format", "json",
        ).stdout)
        self.assertIn("verdict", alias)

    def test_22_outcomes_command_drops_unknown_ids(self):
        f = self.dir / "outcomes_input.json"
        write_json(f, {"outcomes": [
            {"hypothesis_id": "H001", "status": "supported"},
            {"hypothesis_id": "H999", "status": "supported"},
        ]})
        out = json.loads(run(
            "outcomes", "--run", str(self.run_dir), "--file", str(f),
        ).stdout)
        self.assertEqual(out["n"], 1)
        self.assertTrue(any("H999" in w for w in out["warnings"]))
        stored = json.loads(
            (self.run_dir / "meta" / "outcomes.json").read_text(encoding="utf-8")
        )
        self.assertEqual([o["hypothesis_id"] for o in stored["outcomes"]], ["H001"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

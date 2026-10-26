# Changelog

## 1.1.0 — 2026-10-26

### RSI correctness

- **Survivorship bias fixed**: the success denominator now counts rejected hypotheses as failures. Previously `run.leaderboard()` excluded rejected hypotheses and `update_from_run` only counted matched, non-rejected ones, so a strategy could look good by producing many failures that silently disappeared.
- **External outcomes override Elo**: `meta/outcomes.json` (`supported` / `refuted` / `inconclusive`) is now read by `rsi update`. External labels take priority; Elo is the fallback for hypotheses without outcomes.
- **Unscored is explicit**: active hypotheses with no matches are reported as `unscored` and excluded from the success/failure denominator instead of being counted as either.
- **Posterior uncertainty**: each strategy update reports `posterior_ci_low` / `posterior_ci_high`, `success_rate`, `confidence`, and a breakdown (`n_attempted`, `n_evaluated`, `n_rejected`, `n_unscored`, `n_external`).
- **Strategy pools separated**: strategies now carry `pools` (`generation` / `evolution`). `rsi suggest --pool generation` no longer recommends evolution-only operators such as `assumption_repair`.
- **More evolution operators seeded**: `grounding`, `feasibility`, `analogy` are now first-class playbook entries.

### New commands

- `cosci.py outcomes --run RUN --file outcomes.json`: validate and store structured external outcomes. Unknown hypothesis IDs are rejected.
- `cosci.py rsi suggest --pool {generation,evolution,all}`.
- `cosci.py rsi compare --run-a RUN_A --run-b RUN_B`: observational comparison of two completed runs (top-k overlap, review scores, diversity, judge bias, external outcomes). `rsi ab` is an alias.
- `cosci.py rsi update --min-external-samples N`: allow external evidence to update with fewer observations than Elo evidence.

### Docs / tests

- RSI docs now state plainly that a single `quick` run will not show a stable effect; `rsi compare` is explicitly **not** a controlled A/B.
- Added `assets/outcomes.schema.json` and `assets/outcomes.example.json`.
- Added tests for rejected-as-failure accounting, external outcome priority, pool filtering, run comparison, and unknown-ID rejection: **22 tests**.

## 1.0.1 — 2026-10-26

### Fixed

- **Prompt / answer path separation**: `prompt --out` now writes the rendered prompt, while the new `prompt --output-path` controls where the subagent writes its answer. Before this fix, a rendered prompt's `{{OUTPUT_PATH}}` pointed at the prompt file itself, so a compliant subagent would overwrite the prompt.
- **Debate judge contract**: `04_debate_judge.md` now requires `a` and `b` in the output. `ingest --kind matches` also recovers them from `order_presented` and accepts `match_id`, so older judge outputs are not silently dropped.
- **Report RSI section**: `report.md` now reads `meta/retrospective.json` (and renders lessons, failures, edits, risks) instead of looking for the non-existent `retrospective.md`.
- **Meta-review / RSI visibility**: prompts for `meta_review` and `rsi_retrospective` now include rejected hypotheses and their reviews, so failure patterns are visible.
- **Weighted review score**: `review_score` now applies the weights in `goal.json.evaluation_criteria` and renormalises over dimensions actually scored. `review_mean` keeps the unweighted mean for comparison.
- **RSI approval boundary**: `rsi update --apply` no longer appends qualitative lessons without `--approve`; lessons and edits require explicit human approval. Numeric posteriors and the run record can still be applied separately.
- **RSI strategy selection**: `rsi suggest --n 1` no longer forces an exploration pick every time.
- **Path expansion**: `init --out`, `--playbook`, and `Run` paths now expand `~`.
- **Playbook coverage**: added the `assumption_repair` evolution operator to the default strategy set.
- **Debate depth**: `suggest-matches` now writes a `depth` field (`multi` for top-vs-top / same-cluster top matches, `single` for challengers and fallbacks), matching the paper's high-rank multi-turn / low-rank single-turn distinction.

### Changed

- **Accuracy / integrity**: corrected the RSI framing (it is an extension, not an original paper component); annotated N2M-RSI (`arXiv:2505.02888`) as withdrawn; clarified that the paper's wet-lab validation includes negative/limited-effect candidates; added a fidelity table (faithful / extended / not implemented) to `references/architecture.md`; added a retraction check to `references/integrity.md`.
- **Orchestration**: added `--output-path` to all stage examples, documented optional `observation` / `simulation` reviews, and recommended an interim meta-review after each evolution round.
- **README**: rewritten as a GitHub-ready Chinese README with paper mapping, fidelity, installation, quick start, CLI reference, limitations, and FAQ.
- **Examples**: updated the water/nitrogen example to the new prompt/answer path convention.
- **Tests**: expanded from 10 to 17 end-to-end tests covering prompt/answer path separation, judge `a/b` recovery, weighted review scores, retrospective rendering, RSI approval boundaries, RSI edit caps, and rejected-hypothesis visibility.
- **CI**: added a GitHub Actions workflow running the suite on Python 3.10–3.13.

## 1.0.0 — 2026-10-23

- Initial release: file-backed run state, 10 generation strategies, 8 evolution operators, Elo + Bradley-Terry + bootstrap CI, seven-dimension rubric, G0/G2/G3 human gates, cross-run RSI playbook, integrity and safety rules, 10 automated tests.

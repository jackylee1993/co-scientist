# 运行手册：从研究目标到可执行方案

这份手册是给**主 agent（Claude Code）**看的。目标是用最少的上下文、可并行的子 agent、可恢复的文件状态，跑完一次 Co-Scientist + RSI。

## 0. 角色分工

| 角色 | 谁做 | 做什么 | 不做什么 |
|---|---|---|---|
| Orchestrator | 主 agent | 解析目标、渲染提示、派子 agent、收文件、跑脚本、把闸门交给用户 | 不亲自写几十个假设；不替用户做最终判断 |
| Supervisor | 子 agent | 把目标变成 `goal.json` + 评审标准 + 停止条件 | 不生成假设 |
| Landscape | 子 agent | 检索证据、画空白地图 | 不提出假设 |
| Generation | 多个子 agent，每个一个策略 | 产出假设卡 | 不排序、不最终裁决 |
| Reflection | 多个子 agent | 评审、拆假设、红队 | 不重写假设 |
| Debate Judge | 多个子 agent，每场一个 | 两两辩论、打分、裁决 | 不看排行榜锚定 |
| Evolution | 子 agent | 产出后代假设 | 不覆盖亲本 |
| Meta-review | 子 agent | 综合模式、研究总览 | 不生成完整假设 |
| RSI Retrospective | 子 agent | 提炼 lessons、提出 playbook 修改 | 不直接改文件；不碰安全/量表 |
| Scripts | `cosci.py` | 状态、Elo、BT、聚类、报告 | 不做科学判断 |

> 子 agent 用 `general-purpose`（或等价的、有 Read/Write/WebSearch 的 agent），不要用只读的 `Explore`。给子 agent 的提示里写**绝对路径**，并要求它只写指定文件，不改状态文件。

## 1. 准备

```bash
SKILL=~/.claude/skills/co-scientist
RUN=./.co-scientist/runs/my-topic-2026-10-23
PLAYBOOK=~/.co-scientist/playbook
```

确认 Python ≥ 3.10；可选 `pyyaml`（读 YAML goal）。

> `prompt --out` 写的是**渲染后的提示词**；`prompt --output-path` 写的是**子 agent 该把答案写到哪**。两者必须分开，否则子 agent 会覆盖提示词。下文所有 `--output-path` 都是这个含义。

## 2. 阶段与命令

### 阶段 0：Intake（人工闸门 G0）

1. 读用户的研究简报/目标；如果信息严重不足，最多问 3 个问题。
2. 写一个 `goal.yaml`（可参考 `assets/research_goal.example.yaml`）。
3. 初始化：

```bash
python "$SKILL/scripts/cosci.py" init \
  --goal goal.yaml --out "$RUN" --profile standard --playbook "$PLAYBOOK"
```

4. 渲染 supervisor 提示：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name supervisor \
  --out "$RUN/prompts/supervisor.md" \
  --output-path "$RUN/inbox/supervisor.json"
```

5. 派一个子 agent：

```text
Agent(
  description="Supervisor: research plan",
  prompt="Read <RUN>/prompts/supervisor.md and execute it exactly. Write the JSON to <RUN>/inbox/supervisor.json. Do not modify other files."
)
```

6. 把 supervisor 解析合并进 `goal.json`（用户明确给出的约束优先）：

```bash
python "$SKILL/scripts/cosci.py" apply-plan --run "$RUN" --file "$RUN/inbox/supervisor.json"
python "$SKILL/scripts/cosci.py" phase --run "$RUN" --phase 1_landscape --status running
```

7. **G0**：把 `goal.json` 的 `question`、`evaluation_criteria`、`constraints`、`clarifying_questions` 给用户确认。用户确认后继续；如果用户说“你定”，保留 `assumptions` 并在报告里标注。

> 不要让 supervisor 直接覆盖用户明确给出的约束；合并时用户优先。

### 阶段 1：Landscape

1. 渲染：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name landscape \
  --out "$RUN/prompts/landscape.md" \
  --output-path "$RUN/inbox/landscape_main.md"
```

2. 派 1–2 个 landscape 子 agent（一个主检索，一个专门找反例/争议）。让它们各自写 `inbox/landscape_main.md`、`inbox/landscape_counter.md`；主 agent 合并成 `landscape.md`。
3. 检查：≥10 条关键发现、≥3 条争议、≥3 个空白、检索日志完整。
4. 如果检索工具不可用：让 agent 用用户提供的本地材料；在 `landscape.md` 顶部标“未联网，证据不完整”，并降低后续 novelty 置信度。

### 阶段 2：Generation（并行）

1. 选择策略。`rsi suggest` 给出建议；主 agent 再按目标补 2 个策略（至少一个探索型）：

```bash
python "$SKILL/scripts/cosci.py" rsi suggest --playbook "$PLAYBOOK" --n 4 --pool generation
```

> `--pool generation` 只会返回生成策略；`--pool evolution` 留给阶段 5。`combination` / `simplification` / `out_of_box` / `counterfactual` 同时属于两个池，生成阶段可以正常使用。

2. 对每个策略渲染提示：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name generation \
  --strategy literature_gap --round 1 \
  --out "$RUN/prompts/gen_literature_gap.md" \
  --output-path "$RUN/inbox/gen_literature_gap.json"
```

3. 并行派子 agent（每个策略一个），每个写 `inbox/gen_<strategy>.json`。
4. 主 agent 逐个 ingest：

```bash
python "$SKILL/scripts/cosci.py" ingest --run "$RUN" --kind hypotheses \
  --file "$RUN/inbox/gen_literature_gap.json" \
  --strategy literature_gap --agent generation:literature_gap --round 1
```

5. 检查：假设数达到 profile 预算的 60–100%；如果某策略输出 `[]`，换策略补，不要硬凑。
6. 聚类/去重：

```bash
python "$SKILL/scripts/cosci.py" clusters --run "$RUN" --write
```

### 阶段 3：Reflection（分批并行）

1. 先跑 `triage`（快速筛掉明显坏假设），再对通过者跑 `full`，对 top/争议假设跑 `deep` 或 `redteam`；如果预算允许，对 top-3 额外跑 `observation`（能否解释 long-tail 观测）和 `simulation`（逐步模拟机制/实验），这两类是论文明确使用、但默认流程容易漏掉的评审。
2. 每个 batch 3–5 个假设；每个 batch 一个子 agent，避免一个 agent 审 20 个导致质量下降。
2.1 先列出真实 ID，不要凭印象写 `H001`：

```bash
python "$SKILL/scripts/cosci.py" ids --run "$RUN"
```

3. 渲染（`--hypotheses` 支持 `all`、`top:N`、`unreviewed`、逗号分隔的真实 ID）：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name reflection \
  --hypotheses H001,H002,H003 --var REVIEW_TYPE=full \
  --out "$RUN/prompts/reflection_batch1.md" \
  --output-path "$RUN/inbox/reviews_batch1.json"
```

4. ingest：

```bash
python "$SKILL/scripts/cosci.py" ingest --run "$RUN" --kind reviews \
  --file "$RUN/inbox/reviews_batch1.json" --reviewer reflection:full:batch1
```

5. 对 `reject` 的假设：保留记录，但不再安排比赛（除非用户要求复赛）。

### 阶段 4：Tournament（并行）

1. 生成比赛：

```bash
python "$SKILL/scripts/cosci.py" suggest-matches --run "$RUN" --n 20 \
  --policy balanced --out "$RUN/inbox/matches_round1.json"
```

2. 把 `matches` 数组按 3–5 场一组分给判官子 agent。每场渲染一个提示：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name debate_judge \
  --match '<单场 JSON>' --hypotheses H001,H002 \
  --out "$RUN/prompts/debate_M0001.md" \
  --output-path "$RUN/inbox/debate_M0001.json"
```

3. 判官写 `inbox/debate_M0001.json`（输出必须回填 `a`、`b`；只给 `order_presented` 的旧格式也会被兼容）；主 agent ingest：

```bash
python "$SKILL/scripts/cosci.py" ingest --run "$RUN" --kind matches \
  --file "$RUN/inbox/debate_M0001.json" --judge debate:judge1
```

4. 每 20 场看一次：

```bash
python "$SKILL/scripts/cosci.py" rank --run "$RUN" --top 10
```

5. 如果位置偏差显著（p<0.05）或篇幅偏差 flag：对 top-5 的相关比赛交换顺序复赛，把复赛结果作为新比赛加入（不要删旧比赛；Elo 会自然收敛）。

### 阶段 5：Evolution（1–3 轮）

1. 选择：

```bash
python "$SKILL/scripts/cosci.py" select --run "$RUN" --top 5 --diverse 2 \
  --out "$RUN/inbox/selected_round2.json"
```

2. 渲染：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name evolution \
  --hypotheses H001,H003,H004,H007 --round 2 \
  --out "$RUN/prompts/evolution_round2.md" \
  --output-path "$RUN/inbox/evo_round2.json"
```

3. 选算子：`rsi suggest --playbook "$PLAYBOOK" --n 3 --pool evolution` 给出建议；主 agent 再补 1–2 个不同算子。派 2–3 个 evolution 子 agent，各写 `inbox/evo_<operator>.json`。
4. ingest 时指定 parent：

```bash
python "$SKILL/scripts/cosci.py" ingest --run "$RUN" --kind hypotheses \
  --file "$RUN/inbox/evo_feasibility.json" \
  --strategy feasibility --agent evolution:feasibility \
  --parent H001,H003 --round 2
```

5. 新后代必须重新评审、重新比赛；不要直接继承亲本 Elo。

### 阶段 6：Meta-review

1. 渲染：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name meta_review \
  --out "$RUN/prompts/meta_review.md" \
  --output-path "$RUN/meta/meta_review.md"
```

2. 派一个子 agent 写 `meta/meta_review.md`；同目录的 `meta_review.json` 保存末尾 JSON（lessons / judge_biases / blind_spots）。Meta-review 会自动包含被 `reject` 的假设，用于分析失败模式。
3. 检查：是否引用了具体 ID、是否写了盲区、是否最多 3 条 lessons、是否区分观察/推断。
4. 如果跑多轮进化，建议在每轮进化后重复一次本阶段，把上一轮的反复批评喂给下一轮 Generation/Evolution（论文的 meta-review 是持续反馈，不只在最后跑一次）。

### 阶段 7：最终综合（人工闸门 G2）

1. 选 top 5 + 1 个备选：

```bash
python "$SKILL/scripts/cosci.py" rank --run "$RUN" --top 10
python "$SKILL/scripts/cosci.py" select --run "$RUN" --top 5 --diverse 1
```

2. 渲染 final synthesis：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name final_synthesis \
  --hypotheses top:5 --top 5 --out "$RUN/prompts/final_synthesis.md" \
  --output-path "$RUN/meta/experiment_plan.md"
```

3. 子 agent 写 `meta/experiment_plan.md`。
4. 生成报告：

```bash
python "$SKILL/scripts/cosci.py" report --run "$RUN" --top 5 --json
```

5. **G2**：把 `report.md` + `meta/experiment_plan.md` 给用户；用户选择主线/备选/放弃。不要替用户做最终实验决策。

### 阶段 8：RSI 复盘（人工闸门 G3）

1. 渲染：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name rsi_retrospective \
  --out "$RUN/prompts/retrospective.md" \
  --output-path "$RUN/meta/retrospective.json"
```

2. 子 agent 写 `meta/retrospective.json`。
3. 如果有外部结果（实验/专家评审/后续验证），先写结构化结果：

```bash
python "$SKILL/scripts/cosci.py" outcomes --run "$RUN" --file outcomes.json
```

   格式见 `assets/outcomes.example.json`：`status` 只能是 `supported` / `refuted` / `inconclusive` / `unknown`。未知 `hypothesis_id` 会被拒绝写入。

4. Dry-run：

```bash
python "$SKILL/scripts/cosci.py" rsi update --run "$RUN" --playbook "$PLAYBOOK"
```

   重点看 `changes[*]` 里的 `evidence`（external / elo）、`n_rejected`、`n_unscored`、`posterior_ci_low/high`。如果某个策略的 `n` 小于阈值，它不会被更新，这是有意的。

5. 把 `changes`、`lessons`、`proposed_edits` 给用户看；**G3** 后才：

```bash
python "$SKILL/scripts/cosci.py" rsi update --run "$RUN" --playbook "$PLAYBOOK" --apply --approve
```

   有外部结果但样本很少时，可加 `--min-external-samples 1`；这只放宽外部证据，不放宽 Elo 证据。

6. 积累 2 个以上 run 后，可做观察性对照：

```bash
python "$SKILL/scripts/cosci.py" rsi compare --run-a RUN_A --run-b RUN_B --top 5
```

   它会明确标注“不是受控 A/B”；不要用跨 run 的原始 Elo 直接比大小。

### 可选阶段 9：L3 评估器校准

当积累到外部结果（专家评分、实验结果、后续验证）后：

```bash
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name calibration \
  --out "$RUN/prompts/calibration.md" \
  --output-path "$RUN/meta/calibration.json"
```

让子 agent 比较 Elo/评审分数与外部真值，输出 `calibration.json`；任何量表修改都必须由人批准。没有外部真值时不要做 L3，只在报告里标注“自评，未校准”。

## 3. 预算与并行度

| Profile | 假设 | 比赛 | 进化轮 | 生成 agent | 评审 batch | 判官 agent | 预计主 agent 轮次 |
|---|---:|---:|---:|---:|---:|---:|---:|
| quick | 8 | 16 | 1 | 3 | 2 | 2–3 | 6–10 |
| standard | 18 | 60 | 2 | 6 | 3–4 | 4–6 | 15–25 |
| deep | 40+ | 160+ | 3 | 8 | 5–6 | 8–10 | 30–50 |

原则：

- **生成并行**，**评审分批**，**比赛并行但判官独立**。
- 主 agent 不要把完整假设全文反复贴进上下文；用文件路径和 `cosci.py rank` 的摘要。
- 每个子 agent 只负责一个明确产物；输出路径写在提示里。
- 子 agent 之间不共享对话；共享的是文件。

## 4. 停止条件

任一条件满足就停：

- 达到 profile 预算；
- 连续 2 轮没有新增合格假设；
- 所有 top-5 假设都有致命缺陷且无修复路径；
- 连续 2 轮 top-5 Elo 变化 < 10 且无新证据；
- 触发安全/伦理红线；
- 用户叫停；
- 检索/工具不可用，且本地材料不足以支撑判断。

停止不是失败：在报告里写“为什么停”“还缺什么”。

## 5. 断点续跑

- 所有状态在 `run.json`、`tournament.json`、`hypotheses/`、`reviews/`。
- 重跑任何阶段前先 `status` / `validate`。
- 不要重跑已 ingest 的文件（会重复）；先看 `inbox/` 是否已处理。
- 如果 `index.json` 与文件不一致，`cosci.py validate` 会报错；按提示修复，不要手动删状态。

## 6. 失败处理

| 情况 | 处理 |
|---|---|
| 子 agent 输出不是合法 JSON | 让它只输出 JSON 重试一次；仍失败则主 agent 用 Read 修正，不要猜 |
| 生成 agent 卡在联网检索 | 停止它；用已有 `landscape.md` 重新渲染，并在提示开头加“不要联网，只用已有材料，所有未核验证据标 verified:false” |
| 生成假设重复 | `clusters` 看近重复；保留信息量更大的，另一个标 `archived` |
| 评审分数全 7–8 | 要求使用全量程 + 给反例；加一个 redteam reviewer |
| 比赛判官引入新事实 | `validate` 会提示；该场标低置信度，交换顺序复赛 |
| Elo 与评审矛盾 | 看比赛覆盖度；补同簇比较；不要只信 Elo |
| 报告写不出实验方案 | 说明候选不够好；输出“补证据的最小动作”，不要硬写 |
| 用户要求“直接给答案” | 可以缩短流程到 quick，但仍保留假设卡 + 最小验证 + 放弃条件 |

## 7. 成本控制

- quick 用于组会前快速筛选；standard 用于开题/基金；deep 用于重要方向。
- 最贵的不是生成，是**评审和比赛**；先 triage 再 full，能省一半。
- 比赛数量不必覆盖所有组合；优先 top-vs-top、同簇、挑战者。
- 复用 `landscape.md` 和 playbook；不要每个 run 从零检索同一领域。
- 外部结果比更多 Elo 比赛更有价值；把预算花在可验证的 pilot 设计上。

---
name: co-scientist
description: >
  Co-Scientist + RSI 科研工作流：把一个研究目标变成可检验假设，经过生成、对抗评审、Elo/ Bradley-Terry 锦标赛、进化、元评审，最后产出研究方案与 PI 审查单；并用 Recursive Self-Improvement 把跨 run 的策略经验写回 playbook。
  适用于：选题、课题设计、研究假设生成、假设锦标赛、红队评审、实验方案设计、开题/基金素材、判断一个方向值不值得做。
  触发词：Co-Scientist, 合作科学家, 科研假设, 假设生成, 假设锦标赛, 选题, 课题设计, 研究方案, 研究主线, 多智能体科研, 红队评审, 帮我评审这个想法, 这个方向值不值得做, hypothesis generation, hypothesis tournament, research plan, red team my research idea, RSI, Recursive Self-Improvement, Recurrent Self-Improvement, 递归自我改进, 自我改进科研。
  不适用于：单纯文献综述（用 deep-research）、论文润色/写作（用 nature-writing / paper-polish）、数据分析流水线（用 batch-data-pipeline）、纯统计咨询。
---

# Co-Scientist + RSI 科研工作流

参考 Gottweis et al., *Accelerating scientific discovery with Co-Scientist*, Nature 655, 487–496 (2026), DOI [10.1038/s41586-026-10644-y](https://doi.org/10.1038/s41586-026-10644-y)。

一句话：**不是让模型“给几个想法”，而是把假设生成变成一个可审计的锦标赛：多策略生成 → 对抗评审 → 两两辩论 → 进化 → 元评审 → 跨 run 自我改进。**

## 1. 何时使用

用：

- “帮我围绕 X 生成可检验的假设/选题。”
- “这几个方向哪个值得做？帮我做一次假设锦标赛。”
- “红队评审我的研究想法，找出致命缺陷。”
- “把一个模糊的研究目标变成 8 周能做的实验方案。”
- “用 Co-Scientist / 多智能体科研流程帮我选题。”
- “把这次评审的经验记下来，下次自动改进（RSI）。”

不用：

- 只要文献综述/总结 → `deep-research`、`nature-academic-search`。
- 只要写论文/润色 → `nature-writing`、`paper-polish`。
- 批量数据清洗/质检 → `batch-data-pipeline`。
- 纯统计方法咨询 → 直接分析，不需要本流程。
- 需要真实湿实验/临床结论 → 本流程只给假设和设计，不替代实验。

## 2. 核心结构：三层循环

> **调度原则**：主 agent 只做编排、文件交接和闸门；生成、评审、辩论、进化必须派**独立子 agent**（`general-purpose`，有 Read/Write/WebSearch）。让主 agent 自己一口气生成再自己评，会丢掉独立性，也会撑爆上下文。

```text
L1 假设进化（内层）
   Generation → Reflection → Tournament → Evolution → 再比赛
        ▲                                        │
        └──────── Meta-review 反馈 ──────────────┘

L2 策略进化（外层，RSI）
   运行结束 → Retrospective → 小步更新 playbook → 下一次运行加载
        ▲                                        │
        └──── 证据：排名 / 评审 / 外部结果 ──────┘

L3 评估器校准（元层，人工批准）
   判官偏差/评分漂移 → 校准量表与判官提示（只能人改）
```

- L1 让**假设**变好。
- L2 让**找假设的方法**变好（策略权重、检查清单、领域经验）。
- L3 让**尺子**变准（评分量表、判官校准；系统只能建议，不能自动改）。

## 3. 快速开始

```bash
SKILL=~/.claude/skills/co-scientist
RUN=./.co-scientist/runs/my-topic
PLAYBOOK=~/.co-scientist/playbook

# 1) 初始化（goal.yaml 可参考 assets/research_goal.example.yaml）
python "$SKILL/scripts/cosci.py" init --goal goal.yaml --out "$RUN" --profile standard --playbook "$PLAYBOOK"

# 2) 渲染 supervisor 提示（--out 是提示词，--output-path 是子 agent 的答案）
python "$SKILL/scripts/cosci.py" prompt --run "$RUN" --name supervisor \
  --out "$RUN/prompts/supervisor.md" --output-path "$RUN/inbox/supervisor.json"

# 3) 派子 agent 执行，产出写入 inbox/，再 ingest
python "$SKILL/scripts/cosci.py" ingest --run "$RUN" --kind hypotheses --file "$RUN/inbox/gen.json" --strategy literature_gap --agent generation:literature_gap

# 4) 看排名
python "$SKILL/scripts/cosci.py" rank --run "$RUN" --top 10

# 5) 生成报告
python "$SKILL/scripts/cosci.py" report --run "$RUN" --top 5 --json
```

完整阶段、并行方式、闸门见 `references/orchestration.md`。

## 4. 运行协议（必须按顺序）

### 阶段 0：Intake（人工闸门 G0）

1. 读用户的研究简报/目标。信息不足时最多问 3 个**会改变研究设计**的问题。
2. 写 `goal.yaml`：问题、已知/未知、约束、评审标准、明确不做、安全边界。
3. `cosci.py init`。
4. 渲染 `supervisor` 提示（`--output-path "$RUN/inbox/supervisor.json"`），派 1 个子 agent 产出 `inbox/supervisor.json`。
5. 用 `cosci.py apply-plan --run RUN --file inbox/supervisor.json` 合并成 `goal.json`（用户约束优先）；**G0** 给用户确认：问题、约束、评审标准、假设。
6. 用户确认后，`cosci.py phase --run RUN --phase 1_landscape --status running`。

> 用户明确给出的约束优先于 supervisor 的推断；不要偷偷放宽。

### 阶段 1：Landscape

1. 渲染 `landscape` 提示，派 1–2 个子 agent（主检索 + 反例/争议）。
2. 合并成 `landscape.md`：≥10 条关键发现、≥3 条争议、≥3 个空白、检索日志。
3. 每条结论带来源；找不到就标“待补”，禁止编 DOI。

### 阶段 2：Generation（并行）

1. `rsi suggest` 选策略；再手动补至少 1 个探索型策略。
2. 每个策略一个子 agent，渲染 `generation` 提示（`--strategy`），写 `inbox/gen_<strategy>.json`。
3. `cosci.py ingest --kind hypotheses --strategy ...`。
4. `cosci.py clusters --write` 去重/聚类。
5. 目标：达到 profile 预算的 60–100%；某策略产不出就换，不要硬凑。
6. 用户/ PI 可以随时塞自己的假设：写成同 schema 的 JSON，用 `ingest --agent human:PI` 加入；它会和系统假设同场评审、比赛，不会被特殊照顾。

### 阶段 3：Reflection（分批）

1. 先 `triage`，再 `full`，对 top/争议假设加 `deep` / `redteam`。
2. 先 `cosci.py ids --run RUN` 列出真实 ID，不要凭印象写 `H001`；每批 3–5 个假设，一个子 agent；渲染 `reflection` 提示（`--hypotheses ... --var REVIEW_TYPE=...`）。
3. `ingest --kind reviews`。
4. `reject` 的假设保留记录，但不再安排比赛。

### 阶段 4：Tournament（并行）

1. `suggest-matches` 生成比赛（`balanced`：top-vs-top、同簇、挑战者）。
2. 每 3–5 场一组派判官；每场渲染 `debate_judge` 提示（`--match '<json>' --hypotheses Hx,Hy`）。
3. `ingest --kind matches`。
4. 每 20 场看一次 `rank`；位置/篇幅偏差显著时，对 top-5 相关比赛**交换顺序复赛**，旧比赛保留。
5. 新后代必须重新评审、重新比赛，不继承亲本 Elo。

### 阶段 5：Evolution（1–3 轮）

1. `select --top 5 --diverse 2` 选亲本。
2. 渲染 `evolution` 提示；派 2–3 个不同算子的子 agent。
3. `ingest --kind hypotheses --parent Hx,Hy --strategy ... --round N`。
4. 回到阶段 3/4 评审和比赛。

### 阶段 6：Meta-review

1. 渲染 `meta_review` 提示，派 1 个子 agent 写 `meta/meta_review.md`，并把末尾 JSON 写入 `meta/meta_review.json`。
2. 检查：引用具体 ID、写盲区、最多 3 条 lessons、区分观察/推断。
3. Meta-review 默认包含被 `reject` 的假设，用于分析失败模式；多轮进化时建议每轮后追加一次。

### 阶段 7：Final Synthesis（人工闸门 G2）

1. 选 top 5 + 1 个备选。
2. 渲染 `final_synthesis` 提示（`--output-path "$RUN/meta/experiment_plan.md"`）。
3. `cosci.py report --top 5 --json` 生成 `report.md`。
4. **G2**：把报告和实验方案给用户；用户选主线/备选/放弃。不要替用户做最终实验决策。

### 阶段 8：RSI Retrospective（人工闸门 G3）

1. 渲染 `rsi_retrospective` 提示（`--output-path "$RUN/meta/retrospective.json"`）。
2. Dry-run：`rsi update --run ... --playbook ...`，把 changes/lessons 给用户看。
3. **G3** 后：`rsi update ... --apply --approve`。
4. 有外部结果（实验/专家评审）时，先写 `meta/outcomes.json`（`cosci.py outcomes --run RUN --file ...`；自由文本可写 `meta/outcomes.md`）；外部结果优先于 Elo。
5. 可选 L3 校准：用 `10_calibration` 提示词比较 Elo/评审与外部真值；量表修改必须人工批准。

## 5. Profiles

| Profile | 假设 | 比赛 | 进化轮 | 适用 |
|---|---:|---:|---:|---|
| `quick` | 8 | 16 | 1 | 组会前快速筛方向 |
| `standard` | 18 | 60 | 2 | 开题/基金/论文选题（默认） |
| `deep` | 40+ | 160+ | 3 | 重要方向、需要高强度探索 |

原则：**先生成再筛选，不要一上来 deep**；最贵的不是生成，是评审和比赛。

## 6. 文件结构

```text
run/
├── run.json               # profile、seed、phase、counters
├── goal.json              # 目标、约束、评审标准、假设
├── landscape.md           # 证据地形
├── hypotheses/
│   ├── index.json
│   └── H001.json ...
├── reviews/
│   └── H001.full.1.json ...
├── tournament.json        # 比赛、顺序、判官、置信度
├── meta/
│   ├── meta_review.md
│   ├── meta_review.json   # lessons / judge_biases / blind_spots
│   ├── experiment_plan.md
│   ├── retrospective.json
│   ├── outcomes.json      # 结构化外部结果（可选，优先于 Elo）
│   └── outcomes.md        # 自由文本外部结果（可选）
├── inbox/                 # 子 agent 输出
├── prompts/               # 渲染后的提示
├── report.md / report.json
└── run.log
```

Playbook 默认在 `~/.co-scientist/playbook/`，跨项目累积；不要放到 skill 安装目录里。

## 7. 硬规则

1. **可证伪**：每个假设必须有“观察到 X 就放弃”的预测；没有就退回重写。
2. **机制具体**：`mechanism` 是因果链，不是关键词堆叠。
3. **新颖性可辩护**：必须写 `closest_prior_work`；找不到就降置信度，不要把“没查到”当成“新”。
4. **证据可追溯**：`verified: true` 只在实际核对原文/摘要后写；禁止编 DOI/作者/年份/样本量；已撤稿/已更正/被质疑的来源必须标注状态。
5. **区分观察/推断/假设**：报告里明确标注；不把相关写成因果，不把 lab 外推到 field。
6. **可行性是硬约束**：做不完就写低分和 blocker，不要假装 8 周能做完 3 年队列。
7. **多样性**：生成必须有策略差异；至少 20% 探索预算；不要所有假设围绕一个机制。
8. **判官独立**：随机化出场顺序；不看排行榜锚定；不奖励篇幅/自信；关键比赛交换复赛。
9. **人类保留最终决定**：G0/G2/G3 必须给用户；不替 PI 选主线，不自动执行高风险实验。
10. **安全/伦理优先**：人体/动物/危险材料/敏感数据/双重用途，停下转人工；不提供规避监管的建议。
11. **不自动改尺子**：评分量表、安全规则、事实来源只能人改；RSI 只能建议。
12. **不伪造**：数据、文献、审稿意见、实验结果、失败记录，一律不能编。
13. **输出语言跟随用户**：默认中文；用户用英文时，agent 用英文写内容，但 JSON 字段名保持不变。若目标文件里写了 `language: en`，以它为准。
14. **RSI 统计不粉饰失败**：没有外部结果时，被 `reject` 的假设必须计入失败；有 `meta/outcomes.json` 时按 `supported`/`refuted`/`inconclusive` 处理，外部结果优先于 Elo。没有比赛且未 reject 的假设记为 `unscored`，不得混入成功/失败分母。没有外部结果时，Elo 只作弱证据。

## 8. RSI：跨 run 的自我改进

- 运行结束，`rsi_retrospective` 产出 `meta/retrospective.json`。
- `rsi update` 更新每个策略的 Beta 后验。成功/失败按优先级：外部 `supported`/`refuted` > 被 reject = 失败 > 有比赛且进前 25% = 成功 > `unscored` 不计分母。**被 reject 的假设不能从分母里消失。**
- `rsi suggest --pool generation` 只选生成策略，`--pool evolution` 只选进化算子；Thompson sampling 采样。不要把两池的后验均值直接比较。
- 有外部结果时，先写 `meta/outcomes.json`（`cosci.py outcomes --run RUN --file outcomes.json`）；外部结果优先于 Elo，默认 1 条外部结果即可更新（`--min-external-samples 1`）。
- 定性修改（prompt patch/checklist）最多 3 条，必须有本 run 证据，需 `--approve` 才写 `PLAYBOOK.md`。
- 护栏：20% 探索配额、canary 目标、版本/回滚、安全与量表不可自动改、外部结果优先于 Elo。
- **效果预期**：单次 `quick` run 的 RSI 变化主要是探索性的；`standard`/`deep` + 多 run + 外部结果，才可能看到稳定差异。`rsi compare` 只做观察性对照，不是受控 A/B。
- **RSI 是本 Skill 的扩展，不是 Co-Scientist 论文的原始组件**；论文的 self-improving loop 发生在单次运行内（meta-review 反馈），本 Skill 把它延伸到跨 run 的策略层。
- 详细机制与失效模式见 `references/rsi.md`。

## 9. 交付物

一次完整运行至少产出：

1. `report.md`：执行摘要、排行榜、假设卡、多样性、元评审、实验方案、局限。
2. `meta/experiment_plan.md`：推荐主线 + 备选 + 2 周 pilot + 放弃条件。
3. `meta/meta_review.md`：反复批评、盲区、判官偏差、下一轮方向。
4. `meta/retrospective.json`：lessons + playbook 修改建议。
5. `report.json`：机器可读的全量状态。
6. 可选：`meta/outcomes.json`（外部结果，强烈建议有 pilot/专家反馈时写）、`meta/meta_review.json`、PI 审查单（`09_human_gate`）、DOCX/PDF（用 `docx`/`pdf` skill 转换）。
7. 跨 run：`rsi compare --run-a RUN_A --run-b RUN_B` 的观察性对照报告。

## 10. 失败处理

| 情况 | 处理 |
|---|---|
| 子 agent 输出不是合法 JSON | 只输出 JSON 重试一次；仍失败则主 agent 用 Read 修正，不要猜 |
| 假设大量重复 | `clusters` 看近重复；保留信息量更大的，另一个 `archived` |
| 评审全是 7–8 分 | 要求使用全量程 + 给反例；加 redteam |
| Elo 与评审矛盾 | 看比赛覆盖度；补同簇比较；不要只信 Elo |
| 位置/篇幅偏差显著 | 交换顺序复赛；统计显著性；在报告里标注 |
| 报告写不出实验方案 | 说明候选不够好；输出“补证据的最小动作”，不要硬写 |
| 用户要“直接给答案” | 缩到 `quick`，但保留假设卡 + 最小验证 + 放弃条件 |
| 安全/伦理红线 | 立即停；只给合规替代方案；记录“已转人工” |

## 11. 示例触发

- “用 Co-Scientist 帮我把‘河岸带反硝化与厌氧氨氧化的耦合’做成 5 个可检验假设，并排出优先级。”
- “我有三个开题方向，帮我做一次假设锦标赛，找出哪个最可能在 8 周内做出结果。”
- “红队评审这个想法：抗生素扰动后功能基因恢复顺序决定氮去除恢复。”
- “把这次评审的经验记下来，下次生成时自动避开这些坑。”
- “用 RSI 流程，比较这周和上周两次选题会的策略效果。”

## 12. 反例

- “帮我写一篇综述。” → `deep-research` / `nature-writing`。
- “把这段中文润色成英文。” → `paper-polish`。
- “把这 200 个 CSV 清洗合并。” → `batch-data-pipeline`。
- “帮我算一下这个检验的 p 值。” → 直接统计，不需要本流程。
- “直接告诉我哪个药能治癌症。” → 超出假设生成；需要临床证据与监管流程。

## 13. 参考文件

| 文件 | 用途 |
|---|---|
| `references/architecture.md` | 论文架构 → 本 Skill 映射；关键实证与边界 |
| `references/orchestration.md` | 逐阶段命令、并行方式、预算、停止条件 |
| `references/rsi.md` | RSI 三层循环、更新机制、防过拟合、失效模式 |
| `references/evaluation-rubric.md` | 七维评分锚点、裁决、判官偏差控制 |
| `references/integrity.md` | 引文核验、数据分级、伦理、AI 披露、提示注入 |
| `references/water-research-notes.md` | 水资源/环境科学适配：假设类型、混杂、最小验证 |
| `references/prompts/*.md` | 11 个 agent 提示词（含 L3 校准） |
| `assets/*.json` / `*.yaml` | schema、goal 示例、外部结果示例、报告模板 |
| `scripts/cosci.py` | 状态机、Elo/BT、聚类、报告、RSI、run 对照、outcomes |

开始前先读 `references/orchestration.md`；不要凭记忆直接跑完整流程。

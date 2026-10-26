# Co-Scientist 架构 → 本 Skill 的实现映射

来源：Gottweis, Weng, Daryin, Tu et al. *Accelerating scientific discovery with Co-Scientist*. **Nature 655**, 487–496 (2026). DOI: [10.1038/s41586-026-10644-y](https://doi.org/10.1038/s41586-026-10644-y)。开放获取。

论文的系统是 Gemini 2.0 上的多智能体系统，代码未公开；本 Skill 不是复刻其代码，而是把论文中**可迁移的认知结构**做成模型无关、文件驱动、可审计的工作流，并加上受近期 RSI（Recursive Self-Improvement）工作启发的跨 run 外层。RSI 外层是本 Skill 的扩展，**不是论文的原始组件**。

## 1. 论文的四个组件

| 组件 | 论文做法 | 解决的问题 |
|---|---|---|
| 自然语言接口 | 科学家用自然语言给目标、约束、偏好；可随时改目标、给评审、塞自己的假设 | 专家在环，不把判断外包给模型 |
| 异步任务框架 | Supervisor agent 管理 worker 队列，动态分配算力 | 把 test-time compute 变成可扩展的资源 |
| 专用 agent 群 | Generation / Reflection / Ranking / Evolution / Proximity / Meta-review | 把科学方法拆成可分工的子任务 |
| 上下文记忆 | 持久化 agent 状态与系统状态，支持长时推理和重启 | 长程搜索不丢状态 |

### 六个专用 agent（另加 Supervisor）的职责

1. **Generation**：文献探索、模拟科学辩论、迭代假设识别、研究扩展。产出初始假设与提案。
2. **Reflection**：模拟同行评审。初始评审（不检索）→ 完整评审（检索）→ 深度验证（拆承重假设）→ 观察评审（解释 long-tail）→ 模拟评审（逐步模拟机制/实验）→ 循环评审（根据锦标赛结果调整）。
3. **Ranking**：组织 Elo 锦标赛。高分假设多轮辩论，低分假设单轮比较；按相似性和新近性安排比赛。
4. **Proximity**：计算假设相似度图，聚类、去重、辅助比赛安排。
5. **Evolution**：对高分假设做 grounding、连贯性/可行性、从现有假设获得启发（类比）、组合、简化、out-of-box 等进化；**只生成新假设，不覆盖旧的**。
6. **Meta-review**：综合评审与辩论模式，形成 meta-review 反馈给其他 agent；最后生成 research overview。

### 论文的关键实证

- 203 个研究目标上，Elo 随 test-time compute 持续上升，未见饱和。
- 15 个专家 curated 目标上，Co-Scientist 最终超过 Gemini 2.0、o1、o3-mini-high、DeepSeek R1 等基线；11 个目标的盲评中，偏好排名 2.36（越低越好），novelty 3.64/5，impact 3.09/5。
- 消融：Reflection 接入搜索后，已知非新颖想法的 novelty 从 6.14 降到 2.38（更准确），correctness 从 7.4 升到 8.46；Evolution 把 GPQA 从 70.9% 升到 75.4%；Meta-review 把 correctness 评审 AUC 从 0.521 升到 0.597。
- 真实验证：AML 药物重定位（binimetinib、pacritinib、cerivastatin、KIRA6 等）、肝纤维化表观靶点、cf-PICI 基因转移机制。湿实验验证是“生物学现实检查”，不是临床结论；结果包含有效与无效/有限效果的候选。

> 这些数字说明的是**该论文系统在那些任务上的表现**，不能直接外推到你的课题。本 Skill 借用结构，不继承其性能声明。

### 1.1 忠实度：照做、扩展与未实现

| 论文要素 | 本 Skill | 判定 |
|---|---|---|
| Generation 的 4 类策略（文献探索、模拟辩论、迭代假设识别、研究扩展） | 10 个生成策略，覆盖前 3 类；研究扩展由 meta-review 反馈 + `out_of_box` 近似 | 基本覆盖 |
| Reflection 的 6 类评审（initial/full/deep/observation/simulation/recurrent） | 支持 triage/full/deep/observation/simulation/redteam；recurrent 由 meta-review 承担 | 基本覆盖；默认流程只强制 triage + full + deep/redteam |
| Ranking 的 Elo 锦标赛 | Elo + bootstrap CI + Bradley-Terry；判官顺序随机化；位置/篇幅偏差检测 | 扩展（更可审计） |
| 高分假设多轮辩论、低分假设单轮比较 | `suggest-matches` 为每场写 `depth`：top-vs-top / 同簇高分 = `multi`，挑战者/fallback = `single`；判官按 `depth` 执行 | 已实现 |
| 按相似性 + 新近性安排比赛 | `balanced` 策略：top-vs-top、同簇、挑战者；未显式按新近性加权 | 部分覆盖 |
| Proximity 的语义相似图 | TF-IDF 余弦 + 连通分量聚类 | 近似（确定性、零依赖） |
| Evolution 的 6 类算子 | 8 个算子（增加 `assumption_repair`、`counterfactual`） | 扩展 |
| Meta-review 的持续反馈 + 研究总览 | 每轮/结束时生成 meta-review；跨 run 进 playbook | 部分覆盖；默认流程只在阶段 6 运行一次 |
| Supervisor 的异步 worker 队列与动态算力分配 | 主 agent 手动编排 + 并行子 agent + `inbox/` 文件交接 | **未实现真正的异步调度** |
| 工具：web search、领域数据库、私有库、AlphaFold 等专用模型 | WebSearch/WebFetch + 本地文件；可接 MCP/外部工具，但未内置 | **未内置领域工具** |
| 自然语言聊天界面、专家随时改目标/塞假设 | G0/G2/G3 人工闸门 + `ingest --agent human:PI`；无图形界面 | 部分覆盖（文件式 expert-in-the-loop） |
| 湿实验/临床验证 | 只给假设与实验设计，不做实验 | 明确不做 |
| 论文的 Gemini 2.0 规模与 203 目标基准 | 不声称复现；仅在本地跑假设锦标赛 | 不适用 |

**一句话**：这是一个“论文认知结构的开源、可审计、可移植实现”，不是 Google Co-Scientist 的代码复刻，也不能继承论文的 benchmark 数字。

## 2. 本 Skill 的映射

```text
科学家
  │  研究目标 + 约束 + 偏好
  ▼
Supervisor  ──►  goal.json / run.json / 任务队列（文件）
  │
  ├─ Landscape agent ──► landscape.md（证据地形）
  ├─ Generation agents（多策略并行）──► hypotheses/Hxxx.json
  ├─ Reflection agents（triage/full/deep/observation/simulation/redteam）
  │        └──► reviews/*.json
  ├─ Ranking / Debate judges ──► tournament.json（Elo + BT + CI）
  ├─ Proximity（脚本）──► clusters / near-duplicates
  ├─ Evolution agents ──► 新 hypotheses（带 parent_ids）
  ├─ Meta-review agent ──► meta/meta_review.md + JSON lessons
  └─ RSI Retrospective ──► meta/retrospective.json ──► playbook/
  ▼
report.md / experiment_plan.md / PI 审查单
```

| 论文组件 | 本 Skill | 现代化改造 |
|---|---|---|
| Gemini 多智能体 | Claude Code 子 agent + 文件交接 | 模型无关；可用任意前沿模型 |
| 异步 worker 队列 | 主 agent 调度 + 并行 Agent 调用 + `inbox/` | 不依赖专有基础设施；可断点续跑 |
| Elo 锦标赛 | `cosci.py` 的 Elo + bootstrap CI + Bradley-Terry | 排名带不确定性；检测位置/篇幅偏差 |
| Proximity | TF-IDF 余弦聚类 + LLM 语义标签 | 确定性、可复现、零依赖 |
| Meta-review 反馈 | 阶段 6 的 `meta_review.md` + `lessons.jsonl`（可在每轮进化后追加一次） | 反馈跨 run 累积为 playbook（RSI） |
| Evolution | 8 个进化算子（在论文 6 类基础上增加 `assumption_repair`、`counterfactual`，并把 inspiration 具体化为 `analogy`）+ 质量-多样性选择 | 防止模式坍塌；保留探索配额 |
| 论文的固定 agent 提示 | 可版本化、可 A/B 的提示文件 | 提示本身成为 RSI 的优化对象（受护栏约束） |
| 单一 Elo 目标 | Elo 排序 + 加权评审分作为第二意见 + 评审阶段的安全/证据门槛 | 避免把 Elo 当唯一真值；安全不可被优化掉 |

## 3. 为什么用文件而不是共享上下文

1. **上下文预算**：40 个假设 × 评审 × 比赛日志会撑爆任何模型的上下文；文件让主 agent 只读摘要。
2. **可审计**：每个结论都能追到 `Hxxx.json`、`reviews/*.json`、`Mxxxx`。
3. **可并行**：子 agent 不需要互相等待，写入不同 `inbox/` 文件。
4. **可恢复**：进程崩了，重放 `tournament.json` 即可恢复排名。
5. **可移植**：换模型、换机器、换项目，状态仍在。

## 4. 本 Skill 明确不做什么

- 不替代湿实验、田间采样、临床判断。
- 不保证假设为真；它只保证假设**被结构化地生成、攻击、排序和记录**。
- 不自动执行高风险实验；涉及人体/动物/危险材料时必须人工审批。
- 不把 Elo 当客观真值；没有外部结果时，它只是自评代理。
- 不自动改写安全规则或评分量表。
- 不声称 RSI 在单次 run 中有明显效果；RSI 是跨 run 的策略记忆/审计层，需要多 run + 外部结果才能评估。

## 5. 与“deep research”的区别

Deep research 主要做**文献综合**；Co-Scientist 的目标是**产生新的、可检验的假设**。因此本 Skill 的硬门槛是：

- 每个假设必须有机制链和可证伪预测；
- 必须有最小验证；
- 必须与最近先行工作对比新颖性；
- 必须经过对抗性评审和两两比较。

如果用户只是要“总结文献”，用 `deep-research` / `nature-academic-search` 更合适；本 Skill 应用在“选题、假设、研究设计、红队评审”。

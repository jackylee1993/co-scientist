# RSI Playbook（运行时目录）

这里只是模板说明。**实际运行时，playbook 默认写到：**

```text
~/.co-scientist/playbook/
```

原因：它应该跨项目、跨 run 累积，而不是被 skill 安装目录的更新覆盖。

## 初始化

第一次运行 `cosci.py init` 或 `rsi status` 会自动创建：

```text
~/.co-scientist/playbook/
├── PLAYBOOK.md        # 人读的策略记忆（定性修改需 --approve）
├── strategies.json    # Beta 后验、版本、run 计数、策略池、外部证据
├── lessons.jsonl      # 追加式经验（scope / confidence / evidence）
├── runs.jsonl         # 每次 run 的复盘记录
├── canary.jsonl       # 留出目标的表现（建议手动维护 3–5 个）
└── changelog.md       # 版本与回滚点
```

## 初始策略

| 策略 | 池 | 用途 |
|---|---|---|
| `literature_gap` | generation | 从矛盾、long-tail、尺度盲区找假设 |
| `cross_domain_analogy` | generation | 从相邻系统借机制 |
| `assumption_decomposition` | generation | 拆承重假设 |
| `debate_selfplay` | generation | 支持者/怀疑者辩论 |
| `constraint_inversion` | generation | 反转默认约束 |
| `data_driven` | generation | 从数据异常反推机制 |
| `combination` | generation + evolution | 合并高分假设 |
| `simplification` | generation + evolution | 缩到最小可验证核心 |
| `out_of_box` | generation + evolution | 远离高分簇探索 |
| `counterfactual` | generation + evolution | 假设主流解释为错 |
| `assumption_repair` | evolution | 替换被评审判为错误/不确定的承重假设 |
| `grounding` | evolution | 补证据/参数/机制细节 |
| `feasibility` | evolution | 缩到 2 周 pilot 可做 |
| `analogy` | evolution | 从相邻系统借可迁移设计 |

## 更新原则

1. 每次最多 3 条定性修改。
2. 每条必须有本 run 的证据（Hxxx / Mxxxx / 外部结果）。
3. 不修改评分量表、安全规则、事实来源。
4. 保留 20% 探索预算。
5. 用 canary 目标检测过拟合；连续两次下降就回滚。
6. 被 `reject` 的假设计入失败；没有比赛也未 reject 的假设记为 `unscored`，不混入分母。
7. 有 `meta/outcomes.json` 时外部结果优先于 Elo；生成/进化策略按 `pools` 分开采样。

详见 `references/rsi.md`。

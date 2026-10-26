# 示例：河流—河口—河岸带氮去除（quick 演示）

这是给培训现场/第一次使用准备的 20–40 分钟演示。目标不是产出真实研究结论，而是让你看清工作流的每一步在屏幕上留下什么。

## 输入

- `goal.yaml`：研究目标、约束、评审标准（教学示例）
- 可选：你自己整理的 5–10 张论文卡（放在 `RUN/` 或任意本地目录，landscape 阶段读取）
- 可选：一份研究简报（放在 `RUN/goal.md`；否则 `GOAL_MD` 回退到 `goal.yaml` 的 `question`）

## 跑法

```bash
SKILL=~/.claude/skills/co-scientist
RUN=./.co-scientist/runs/nitrogen-demo
PLAYBOOK=~/.co-scientist/playbook

python "$SKILL/scripts/cosci.py" init \
  --goal "$SKILL/examples/water_nitrogen/goal.yaml" \
  --out "$RUN" --profile quick --playbook "$PLAYBOOK"
```

然后在 Claude Code 里说：

```text
读取 ~/.claude/skills/co-scientist/references/orchestration.md，
按 quick profile 跑完 RUN=.../nitrogen-demo：
1) supervisor 解析目标；2) landscape 用本地论文卡 + 公开检索；
3) 3 个策略各生成 2–3 个假设；4) triage + full 评审；
5) 16 场锦标赛；6) 1 轮 evolution；7) meta-review + report。
G0 和 G2 停下来给我看。渲染提示时注意 --out 是提示词、--output-path 是子 agent 的答案。
引用只能用真实来源，找不到就标“待补”。
```

## 现场讲解点

| 阶段 | 屏幕上看什么 | 讲解点 |
|---|---|---|
| Supervisor | `goal.json` 的评审标准 | 先把“什么叫好答案”定下来，再生成 |
| Landscape | `landscape.md` 的争议/空白表 | 不是文献综述，是找可打的缺口 |
| Generation | 3 张假设卡 | 机制链 + 可证伪预测 + 最小实验 |
| Reflection | `reviews/*.json` 的 fatal_flaws | 先杀坏假设，再谈优点 |
| Tournament | `rank` 的 Elo + CI | 不是选“最漂亮”，是选“最可判决” |
| Evolution | 后代的 `provenance.changes` | 进化不是堆字数，是补漏洞/缩范围 |
| Meta-review | 盲区 + 判官偏差 | 系统也要审查自己的评审 |
| RSI | `retrospective.json` + `PLAYBOOK.md` | 下次生成自动避开这次踩的坑 |

## 注意

- 演示中的假设是教学示例，不是任何课题组的真实研究计划。
- 不要上传未公开数据；`goal.yaml` 已把数据边界写成“内部/公开混合”。
- 现场网络不可用时，landscape 只读本地论文卡，并在 `landscape.md` 顶部标“未联网”。
- 不要用演示结果直接写论文/开题；它只给候选和验证设计。

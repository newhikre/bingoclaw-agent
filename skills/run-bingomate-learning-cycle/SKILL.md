---
name: run-bingomate-learning-cycle
description: 缤果学伴（BingoMate）完整智能教辅闭环。用《2026 初中53同步》英语九年级 Unit 1–2 完成学生建档与摸底画像、A/B/C 内部策略判定、按画像生成个性化学习任务、记录真实作答、生成学生/家长/教师三类学习报告，并把本次观测自动合并回画像。当用户说“开始学英语”“先摸个底”“给我安排今天的练习”“根据学情出题”“继续上一轮”“今天学完了”“生成学习报告”“同步给家长”，或需要在类似 OpenClaw 的平台演示从画像到任务再到报告的端到端流程时使用。也用于接入已有画像、任务包或作答记录后从对应阶段继续。本 skill 自包含题库和脚本，不依赖另外三个 BingoMate skill。
---

# 缤果学伴完整学习闭环

把原来的画像建模、分层任务和报告成文合并为一个可持续会话。始终围绕同一份状态文件工作，避免阶段之间丢失画像、自定义题定义、任务包、提示记录或画像回写。

## 面向用户的交互红线

默认当前对话对象是学生或家长。除非调用方明确要求教师端明细或产品调试信息，否则**所有发出的消息都按用户可见内容处理**，包括工具调用前后的过渡语、进度说明和结果总结，而不只是最终报告文件。

- 静默执行状态读取、脚本调用、判分、格式修复、文件写入和画像回写。调用工具前不要发“我先检查”“现在运行”“接下来调用”等过程预告，工具完成后也不要复述命令、文件或阶段流转。
- 严禁说“报告流程清楚了。现在跑报告命令，让脚本判分并生成三份文字报告”“正在读取画像”“进入报告阶段”“脚本已经匹配到答案”等内部旁白。
- 用户只应看到四类内容：需要回答的建档问题、要完成的题目、针对作答的自然讲解、可直接阅读的学习结果或下一步安排。
- `phase`、JSON、命令、脚本名、文件名、字段名、A/B/C、判分方式、状态更新和内部异常只能留在工具与内部上下文中，不得作为聊天内容输出。
- 内部修复成功后直接继续正常教学；确实无法继续时，只用自然语言说明“这次记录还需要整理，请稍后再试”，不得附技术原因或调试过程。
- 每次准备发送消息前先问：**这句话是在帮助学生学习，还是在描述系统怎样工作？** 后者不发送。

完整用户文案规则见 [references/copy-policy.md](references/copy-policy.md)，其约束适用于整个对话过程。

## 核心规则

1. 维护一个调用方指定位置的 `state.json`，让画像、学习轮次和报告属于同一个学生、同一次学习链路；运行态文件不写进 Skill 目录。
2. A/B/C 只用于系统排课。对孩子和家长只说练什么、怎么练，不说代号或层级名。
3. 数字由脚本计算。先修输入，再判分；不因格式或题号问题改为手算。
4. 技术问题只在内部处理。面向学生的消息不出现 `qid`、字段、脚本、答案匹配、模型代判、报错原文等信息。
5. 不编题目、作答、出处或历史趋势。没有真实作答时不生成学习报告。
6. 报告完成后自动合并 `profile_patch`，但不自动更改 A/B/C；策略变化必须重新摸底。
7. 所有用户可见话术以 [references/copy-policy.md](references/copy-policy.md) 为唯一规则：学生和家长只显示单元粒度，内部仍保留精确定位。
8. 每道任务强制有会话内唯一 `item_id`；作答用 `responses[].item_id` 关联，`seq` 只用于展示，`qid` / `locator` 只用于回源。

## 阶段路由

以下命令和阶段值全部用于内部编排，不向学生或家长展示，也不在运行前发送过程性说明。先静默运行：

```bash
python scripts/cycle_engine.py status --state state.json
```

按返回的 `phase` 只执行一个阶段：

| phase | 当前状态 | 下一步 |
| --- | --- | --- |
| `needs_diagnostic` | 尚无有效画像 | 建档、摸底并运行画像判定 |
| `ready_for_task` | 已有画像，无待完成任务 | 按画像生成一轮任务 |
| `awaiting_responses` | 已发任务，尚无作答记录 | 讲题并记录原始作答与提示 |
| `needs_internal_repair` | 作答或任务格式需恢复 | 内部修复后重试，不向学生解释 |
| `ready_for_report` | 至少一轮真实作答已归一化 | 生成报告；确需同次学习再练一轮时显式继续 |

用户只要求某一阶段时，在该阶段交付后停止。用户要求完整演示时，阶段之间仍要等待真实回答；不得替学生作答后一路跑完。

## 第一次启动或接入已有画像

新建状态：

```bash
python scripts/cycle_engine.py init --state state.json --learner learner.json
```

`learner.json` 可省略。若用户已经有旧链路产出的画像，直接接入：

```bash
python scripts/cycle_engine.py adopt-profile --state state.json --profile profile.json
```

完整状态契约与命令见 [references/state-contract.md](references/state-contract.md)。

建档中的 `learned_units`、`available_minutes` 与 `feedback_preference` 会自动补进诊断会话；诊断文件里的显式值优先。不得重复询问已经保存的信息。

## 阶段一：建档、摸底与画像

进入本阶段时读：

- [references/diagnostic-workflow.md](references/diagnostic-workflow.md)
- [references/profile-schema.md](references/profile-schema.md)
- 生成题前再读 [assets/scope/Unit1-2_考点范围.md](assets/scope/Unit1-2_考点范围.md)

执行顺序：

1. 只强制询问已学单元与可用时长；姓名、偏好等答多少记多少。
2. 用 2–3 道教辅锚题校准，其余题补齐能力维度。一次发完，不在摸底中提示或报对错。
3. 生成题同时保存题目定义、标准答案和自定义 `qid`；不得只保存 `qid + response`。
4. 收齐作答后静默运行，不先向用户说明脚本或判分流程：

```bash
python scripts/cycle_engine.py diagnose --state state.json --session diagnostic-session.json
```

5. 若标准答案不能覆盖生成题的等价表达，按输入契约在内部补 `model_judgment` 后重试。教辅锚题不得被模型覆盖。
6. 向孩子反馈做对多少、强弱点和下一步安排，不展示 JSON 或技术恢复过程。

## 阶段二：按画像生成学习任务

进入本阶段时依次读：

1. [references/task-workflow.md](references/task-workflow.md)
2. [references/profile-contract.md](references/profile-contract.md)
3. [references/bank-contract.md](references/bank-contract.md)
4. 只读画像 `strategy.code` 对应的 `tier-a.md`、`tier-b.md` 或 `tier-c.md`
5. 需要变式时读 [references/variant-generation.md](references/variant-generation.md)
6. 交付前读 [references/output-format.md](references/output-format.md)

任务生成必须满足：

- 直接消费画像里的策略，不重新判层。
- 只出 `scope.units` 内的内容；题库与锚题表都在本 skill 的 `assets/`。
- 优先教辅原题，候选不足才生成变式；所有题都带 `item_id` 与 `acceptable_answers`。
- `session.units` 与每题单元必须落在画像 `scope.units`；`part`、`ability`、`source` 以及变式/生成题自检字段都要通过脚本门禁。
- 默认不重复已用教辅原题；只有明确订正时才写 `repeat_for_correction: true` 和 `repeat_reason`。
- `confidence=low` 时只降难度配比，不改讲解风格。
- 每轮只攻一个主锚点，记录题目为什么出现。
- 对学生隐藏答案、内部策略和产品说明。

生成 `task-pack.json` 后静默记入状态，再直接把题发给学生：

```bash
python scripts/cycle_engine.py append-pack --state state.json --pack task-pack.json
```

只要求出任务时到此停止。

## 阶段三：讲题并记录真实作答

边讲边记录每道题的：

- `item_id`（主键）
- `seq`（展示序号，兼容旧日志）
- 学生原样 `response`
- `hints_used`，没有提示才写 `0`
- 可选 `seconds`

不要替学生修拼写、改大小写或补答案。推了新一批题就是新一轮，不能覆盖上一轮。

一轮结束后静默运行：

```bash
python scripts/cycle_engine.py append-log --state state.json --log raw-log.json
```

该命令会先执行入口修复。返回 `visibility: internal` 或 `phase: needs_internal_repair` 时，在内部补齐任务定义、标准答案或题目对应关系后重试；不要把修复过程发给学生。

通常一轮后直接出报告。若孩子在同一次学习里确实还要做第二轮，先生成新任务包，再显式追加：

```bash
python scripts/cycle_engine.py append-pack --state state.json --pack round2-pack.json --continue-before-report
```

之后照常 `append-log`，最终报告会汇总所有尚未报告的轮次，同时保留逐轮明细。不要为了展示多轮而虚构第二轮作答。

## 阶段四：报告成文与画像更新

进入本阶段时读：

- [references/report-workflow.md](references/report-workflow.md)
- [references/report-format.md](references/report-format.md)
- [references/intake-repair.md](references/intake-repair.md)
- [references/report-copy.md](references/report-copy.md)

在内部静默运行；不要发送“现在跑报告命令”“让脚本判分”“生成三份报告”等过渡语：

```bash
python scripts/cycle_engine.py report --state state.json --out-dir report-out
```

一次完成：

1. 汇总所有已完成轮次并按标准答案判分。
2. 生成结构化 `report.json`。
3. 生成 `student.txt`、`parent.txt`、`teacher.txt`。
4. 把用过的题、最新掌握观测、重点能力和错误模式合并回状态内画像。

学生版与家长版不出现层级、技术语言、样本不足术语或内部修复。教师版可讲学习路线和样本量，但不展示代码字段名。报告后的画像更新只改变后续选题依据，不擅自改变策略。

完成当前任务和报告后，若真的要重新摸底并允许策略变化，运行：

```bash
python scripts/cycle_engine.py rediagnose --state state.json --session diagnostic-session-2.json
```

旧策略会进入历史，已用题排除项继续保留；无有效作答时旧策略不变。

## 资源索引

| 路径 | 用途 |
| --- | --- |
| `scripts/cycle_engine.py` | 唯一推荐入口：状态、阶段、报告与画像合并 |
| `scripts/profile_engine.py` | 诊断判分与 A/B/C 策略 |
| `scripts/report_engine.py` | 多轮学习判分与结构化报告 |
| `scripts/intake.py` | 作答记录归一化 |
| `scripts/compose.py` | 三类纯文字报告 |
| `assets/questions/` | Unit 1–2 带答案题库 |
| `assets/anchor_bank.json` | 12 道稳定锚题定位 |
| `assets/scope/` | 生成题与变式题边界 |

## 自检

```bash
python scripts/cycle_engine.py validate
```

修改任何脚本、契约或题库后都要运行。它会检查锚题、自定义 `qid`、多轮报告、入口修复和画像自动合并。

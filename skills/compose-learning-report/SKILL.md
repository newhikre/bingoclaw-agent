---
name: compose-learning-report
description: 缤果学伴（BingoMate）教辅链路的末端环节，把一次学习的作答记录变成一份文字版学习报告。接住上游 student-ability-tiering 交付的任务包与授课记录，先修复作答记录的格式问题，再调脚本判分，最后产出三份纯文字报告：给孩子的收尾话术、给家长的同步、给老师和产品的明细。当用户说「出份文字版报告」「生成学习报告」「今天学完了，把报告写出来」「把报告发给家长」「这次练得怎么样，写成文字」「报告成文」「收尾」时使用；也用于上游报告 JSON 已经算好、只差落成文字的场景，以及作答记录格式对不上导致 report_engine 报错或判出「全未作答」时的排查修复。本环节不重判层级、不重算正确率、不编作答记录，判分一律以 report_engine 的结果为准。
---

# 学习报告成文

链路四段的最后一段：**教辅数据录入 → 建立画像（`build-learner-profile`）→ 分层与任务生成（`student-ability-tiering`）→ 报告成文（本技能）**。

本技能只做三件事，顺序不能颠倒：

1. **修入口**——把上游那份形状不稳的作答记录归一化成 `report_engine` 认识的样子
2. **调脚本判分**——数字全部由脚本算
3. **成文**——把报告 JSON 渲染成三份给人读的纯文字

## 为什么第一步是修入口

授课记录是执行任务的那个智能体**在真实对话里边讲边记**的，不是填表填出来的，形状写飘是常态而不是意外。而 `report_engine` 只认一个键：

```python
records = {r.get("seq"): r for r in log.get("responses", [])}
```

写成 `{"items": [...]}`，`log.get("responses", [])` 返回空列表，**每道题都拿到 `record=None`，全部判成「未作答」，脚本不报错、正常出报告、所有数字是零**。微信通道实测已经踩过这个坑：当时误判成 qid 与 locator 的映射问题，最后改成手工核对凑了一份报告——那正好违反了「以脚本结果为准、不手算」。

所以本技能的第一动作永远是跑 `intake.py`，不管上游给的记录看起来多正常。

## 工作流

### 第一步，归一化作答记录

一轮一份，推了几轮就跑几次：

```bash
python scripts/intake.py --pack round1_pack.json --log round1_raw_log.json --out round1_log.json
```

脚本输出 `{"ok", "repairs", "issues"}`：

- **`repairs`** 是已经自动修掉的，照单收下，但**要在最后的判定说明里如实列出来**，不藏着。
- **`issues` 非空就停下**（退出码 2）。这些是脚本不敢替你猜的东西，必须由人处理。

能自动修的形状、以及 `issues` 各自怎么处理，见 [references/intake-repair.md](references/intake-repair.md)。

**`issues` 里最常见的一条是任务包缺 `acceptable_answers`。** 教辅原题要回源题库填上，生成题必须自带。**绝不能凭印象补答案**——补错了整份报告的数字都是错的，而且看不出来。

### 第二步，判分

归一化之后交给上游的脚本，本技能不自己判分：

```bash
python ../student-ability-tiering/scripts/report_engine.py append --session session-小宇.json --pack round1_pack.json
python ../student-ability-tiering/scripts/report_engine.py append --session session-小宇.json --log round1_log.json
python ../student-ability-tiering/scripts/report_engine.py report --session session-小宇.json --profile profile.json > report.json
```

上游已经把这一轮 `append` 进会话文件的话，就只跑最后一句。

**拿到 `{"ok": false, ...}` 时不要把它当报告往下渲染**，那是错误信封。回到第一步看 `issues`。

**判分口径一条都不改**：用了提示才做对的不计入独立掌握；报告同时给 `accuracy_independent`（回写画像用）与 `accuracy_with_hints`（对孩子说的那个）。

### 第三步，成文

```bash
python scripts/compose.py --report report.json --profile profile.json --name 小宇 --out-dir out/
```

产出三份纯文本：

| 文件 | 给谁 | 要点 |
| --- | --- | --- |
| `student.txt` | 孩子 | 按层风格，四段式，**不出现层级** |
| `parent.txt` | 家长 | 只讲事实与下一步，**不评价孩子**，不出现层级 |
| `teacher.txt` | 老师、产品 | 可以出现学习路线、样本量、待写回档案的内容 |

`--name` 缺省时用中性称呼，**不编造名字**。画像若已携带 `learner.name` 就从那里取。

`--profile` 省略就没有维度级的变化量。**建议一直传**——报告 JSON 自带的 `mastery_delta` 只到难度层，而 `focus_abilities` 是按能力维度选的，不传画像就回答不了「阅读到底进步没有」。

三份文字的完整措辞规矩、按层的风格差异与红线，见 [references/report-copy.md](references/report-copy.md)。**直接交付脚本产出的文字即可**；确实需要润色时只能改措辞，不能改数字、不能加脚本没给的结论。

**三份文案里都不许出现技术语言**，教师版也一样。字段名（`seq`、`qid`、`profile_patch`）、模块名（`build-learner-profile evaluate`）、代码字面量（`None`、`['a','b']`）、内部口径词（计分点、独立正确率、置信度、降级模式、画像补丁）全部要换成业务说法。`compose.py` 已经做了替换与过滤，人工润色时不要加回去。对照表见 report-copy.md。

## 三条红线

**一、不手算。** 脚本跑不通就修输入，修不了就把 `issues` 交回给调用方。手工核对出来的数字翻不回具体那道题，也回写不了画像——一份翻不回去的报告没有存在价值。

**二、不跨会话。** 本链路没有存历史，「本周第一次提升」「连续三天下滑」这类话没有数据支撑。**唯一允许的对比是「本次 vs 摸底」**，因为 `mastery_delta` 的 `before` 来自画像，那是有据可查的。

**三、层级不出口。** 代号 `A`/`B`/`C` 与层级名（拓展挑战/巩固提升/稳固基础）都不进 `student.txt` 和 `parent.txt`。层级是给系统排课的，不是孩子的身份；家长拿到会转述给孩子，效果和当面说一样。要表达怎么练就直接说题量与配比。

## 掌握度对人说话时用五档，不用百分比

方案 3.1 的五档：`已掌握 / 基本掌握 / 薄弱 / 严重薄弱 / 未接触`。

一次会话通常只有 6–8 个计分点，摊到四个能力维度上，**每个维度常常只有 1–2 个点**。此时「汉英转换 100%」是一道题算出来的，「基础夯实 −33.4」是三个点里对 1 变成对 2。百分比会给出它撑不起的精度感，家长还会拿去跟别人家孩子比。

所以 `compose.py` 对人说话时一律输出五档，**计分点少于 3 的维度直接不下判断**，只在 `teacher.txt` 里标出 `n` 和「样本不足，不进升降层证据」。百分比仍然保留在报告 JSON 里给系统读。

## 降级模式

报告 JSON 的 `bank_available` 为 `false` 时，本次全是生成题，**出处一律不提课时与题号，只说考点**。`compose.py` 已经据此处理，`locator` 为 `null` 的题也一样。

编一个「Unit 1 第 3 课时第 2 题」出来，孩子翻开教辅发现对不上，比不给出处严重得多。

## 几条不做的事

- **不重判层级。** `tier_recommendation` 是建议加证据，判定权归 `build-learner-profile evaluate`。单次会话最多 8 个计分点，据此升降层比不改更危险。
- **不写画像。** 只把 `profile_patch` 原样带进 `teacher.txt`，合并是调用方的动作。没人合并就意味着下一轮推同一批题。
- **不合并轮次。** 推了几次就有几条记录，两轮的表现不揉成一个数——第 1 轮错、第 2 轮同考点对，两条都留着才看得出「是讲完才会的」。
- **不编作答记录。** 没有记录就停下来索要，不用「大概做对了四道」这类转述凑一份。
- **不给孩子看 JSON。**

## 已知缺口

- 微信等纯文本通道里 markdown 不渲染，所以三份报告都是纯文本，不用 `#` 标题和表格。需要富文本渲染时另做一层，不要改脚本输出。
- `intake.py` 按出现顺序兜底对题号时会在 `repairs` 里标「请人工确认」——上游记录顺序与任务包不一致时会对错，这是兜底不是保证。
- 维度级的变化量依赖 `--profile`，而画像里的 `mastery.by_ability` 是摸底那次的值；`profile_patch` 没人合并的话，第二轮之后的「较摸底」始终以摸底为基准，不是相对上一轮。
- 报告只覆盖本次会话，遗忘曲线复习调度与周度升降层都不在本环节。

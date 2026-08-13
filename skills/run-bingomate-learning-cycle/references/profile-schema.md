# 用户画像建模 —— 输入与输出契约

四段式 demo 第二段的契约。原则：**调用方只提交原始作答，脚本负责回源判题并产出画像**。

## 输入：诊断会话

教辅锚题只提交 `qid + response`。不要传入 `correct`、`part`、`ability`，这些字段由脚本从带答案题库回源生成。

```json
{
  "session_id": "bm-20260806-0193",
  "generated_at": "2026-08-06T15:20:00+08:00",
  "scope": {
    "units": ["Unit 1", "Unit 2"]
  },
  "profile": {
    "available_minutes": 20,
    "feedback_preference": "先给思路"
  },
  "responses": [
    {"qid": "u1-method-by", "response": "D"},
    {"qid": "u2-shot-past", "response": "shot"}
  ]
}
```

`response` 可为选项字母、选项文本或填空答案。未作答也要保留记录并传 `null`，脚本会按未答处理。

若诊断包含 LLM 生成题，必须同时提交题目定义。推荐把生成题保存在顶层 `questions`，自定义 `qid` 可以自由命名；`id` 也兼容：

```json
{
  "questions": [
    {
      "qid": "gen-u1-by-03",
      "unit": "Unit 1",
      "part": "基础夯实",
      "type": "单项选择",
      "ability": "语法辨析",
      "knowledge_point": "by + 动名词",
      "acceptable_answers": ["B", "by reading"],
      "misconception_map": {
        "A": "把 by 后的动名词误作不定式"
      }
    }
  ],
  "responses": [
    {"qid": "gen-u1-by-03", "response": "A"}
  ]
}
```

如果平台不方便在对话状态里分别保存 `questions` 和 `responses`，可使用自包含作答，把生成题定义贴在对应的 `question` 中：

```json
{
  "scope": {"units": ["Unit 1"]},
  "responses": [
    {
      "qid": "my-custom-qid-01",
      "response": "B",
      "question": {
        "unit": "Unit 1",
        "part": "基础夯实",
        "type": "单项选择",
        "ability": "语法辨析",
        "knowledge_point": "by + 动名词",
        "acceptable_answers": ["B", "by reading"]
      }
    }
  ]
}
```

自由主题的生成阅读仍要完整保存题目定义。建议两个计分小题共用一个 `passage_id`，每个小题各有自己的 `qid`、`acceptable_answers`，并可附上仅供内部复现与去重的元数据：

```json
{
  "qid": "gen-reading-01-detail",
  "passage_id": "reading-bm-20260806-0193-01",
  "unit": "Unit 1",
  "part": "能力提升",
  "type": "阅读理解",
  "ability": "篇章理解",
  "acceptable_answers": ["B"],
  "generation_meta": {
    "variation_seed": "bm-20260806-0193",
    "topic": "a school garden project",
    "question_type": "细节理解"
  }
}
```

`generation_meta` 不参与判分，也不展示给学生。它只用于保证同一 `session_id` 复现同一篇、不同会话不复用旧阅读；`topic` 是模型本次自由创作后的记录，不是预设主题池。

兼容性约定：

- 顶层题目定义支持 `questions`（推荐）和 `generated_items`（旧名称）
- 自定义题号支持 `qid` 和 `id`，数字题号也会统一转成字符串匹配
- 标准答案推荐 `acceptable_answers`，也兼容单题里的 `answers` 或 `answer`
- 顶层 `answers` 仍是旧的**作答集合**字段，已停用；学生作答必须放在 `responses`

### 模型判题降级（仅内部）

优先匹配标准答案。仅当自定义生成题缺少可解析答案，或学生答案可能是答案表之外的等价表达时，模型独立解题并把结论结构化后交回脚本：

```json
{
  "scope": {"units": ["Unit 1"]},
  "questions": [
    {
      "qid": "custom-u1-translation-02",
      "unit": "Unit 1",
      "part": "基础夯实",
      "ability": "汉英转换"
    }
  ],
  "responses": [
    {
      "qid": "custom-u1-translation-02",
      "response": "by reading aloud",
      "model_judgment": {
        "correct": true,
        "reason": "表达符合题意，语法和语义均成立"
      }
    }
  ]
}
```

约束：

- `model_judgment` 只允许用于自定义生成题；教辅锚题始终按题库答案判定
- 必须包含布尔值 `correct` 和非空 `reason`；`reason` 只供内部审计，不进入学生反馈
- 标准答案已经判对时忽略模型结论；只有未匹配时才启用降级
- 使用过模型判题时，整套画像置信度最高为 `medium`
- 模型也无法可靠判断时，把题目标成 `"score_eligible": false`，并在该作答写 `"model_judgment": {"status": "ungradable", "reason": "..."}`；脚本会保留审计线索但跳过计分

`response` 为 `null`、空字符串或空对象时按未作答处理：不算错、不进入分母、不抬高置信度，也不写进 `exclude_qids`。如果整套都没有可评分作答，脚本只产出低置信度临时 B，统一状态仍停留在 `needs_diagnostic`。

缺少 `responses`、出现未知 `qid` 或自定义题定义不完整时，脚本返回 `visibility: internal` 的修复信息。调用方必须在内部修复或走上述降级流程，不能把错误原文、`qid`、脚本执行过程或判题方式展示给学生。自定义 `qid` 本身不是错误；只有作答与题目定义之间的关联丢失才需要恢复。

## 输出：学习者画像

只保留 demo 端到端跑通必需的字段：

```json
{
  "session_id": "bm-20260806-0193",
  "generated_at": "2026-08-06T15:20:00+08:00",

  "scope": {
    "units": ["Unit 1", "Unit 2"]
  },

  "profile": {
    "available_minutes": 20,
    "feedback_preference": "先给思路"
  },

  "mastery": {
    "基础夯实": 66.7,
    "能力提升": 50.0,
    "by_ability": { "词形变化": 100, "汉英转换": 50, "语法辨析": 0, "篇章理解": 50 }
  },

  "characteristics": {
    "strengths": ["词形变化"],
    "weaknesses": ["语法辨析", "汉英转换"],
    "error_pattern": "知识型",
    "misconceptions": ["by 后的动名词误作不定式"],
    "evidence": { "item_count": 8, "correct": 5 }
  },

  "strategy": {
    "code": "B",
    "name": "巩固提升",
    "confidence": "medium",
    "reason": "基础夯实达标、能力提升不足；最弱维度为语法辨析"
  },

  "next_session": {
    "difficulty_mix": { "基础夯实": 60, "能力提升": 40 },
    "feedback_style": "先给思路再分步展开",
    "item_count": 6,
    "focus_abilities": ["语法辨析", "汉英转换"],
    "focus_units": ["Unit 2", "Unit 1"],
    "exclude_qids": ["u1-method-by", "gen-u1-by-03", "u2-clause-that"]
  }
}
```

---

## 字段说明

### 顶层

| 字段 | 含义 |
| --- | --- |
| `session_id` | 会话 ID，贯穿四个环节 |
| `generated_at` | 生成时间 |

### `scope` —— 结论的适用范围

| 字段 | 含义 |
| --- | --- |
| `units` | 本次评估的单元。只纳入学生确认已经学过的单元 |

### `profile` —— 自报档案

自报信息**不参与打分**（v3.0 议题一已承认学生自评不可靠），只做风格参数。

| 字段 | 含义 |
| --- | --- |
| `available_minutes` | 单次可用分钟数，决定 `next_session.item_count` 上限 |
| `feedback_preference` | `先给思路` / `直接给答案` |

### `mastery` —— 掌握程度

| 字段 | 含义 |
| --- | --- |
| `基础夯实` / `能力提升` | 两个难度层的正确率，判 ABC 用。难度层直接取教辅编者标注的 `part`，不另建难度模型 |
| `by_ability` | 四维正确率，定薄弱点用 |

### `characteristics` —— 对学生的表述

画像模块的核心产出。**逐题作答不出现在这里**——原始日志属于会话记录，画像交付的是从日志里读出来的结论。

| 字段 | 含义 |
| --- | --- |
| `strengths` | 表现好的能力维度 |
| `weaknesses` | 薄弱维度，按严重程度排序 |
| `error_pattern` | `知识型` / `粗心型` / `思路型`，见下方判定规则 |
| `misconceptions` | 典型误解，人话表述。报告直接引用，无需再翻译 |
| `evidence` | `{item_count, correct}`，两个数。支撑 `confidence`，也让报告能说「8 题对 5 题」 |

**`error_pattern` 判定规则**（可由 `part` + `ability` + `correct` 确定性算出）

| 模式 | 特征 | 对应策略 |
| --- | --- | --- |
| 知识型 | 同一维度反复错 | 回到该维度补概念 |
| 粗心型 | 基础夯实错、能力提升反而对 | 不降难度，加校对环节 |
| 思路型 | 基础全对、能力提升篇章错 | 单点会但整合不会，练串联 |

### `strategy` —— 判定结果

| 字段 | 含义 |
| --- | --- |
| `code` | `A` / `B` / `C` |
| `name` | `拓展挑战` / `巩固提升` / `稳固基础` |
| `confidence` | `high` / `medium` / `low` |
| `reason` | 判定理由，一句话 |

**判定规则**（本教辅只有两个难度层）

- `C · 稳固基础` —— 基础夯实正确率偏低
- `A · 拓展挑战` —— 基础夯实稳，且能力提升篇章能做对
- `B · 巩固提升` —— 其余情况

不按 20%/50%/30% 强制分布。

**置信度**：作答 ≥8 题且两个难度层都测到 → `high`；≥4 题 → `medium`；否则 `low`。整套无教辅锚题时封顶 `medium`（LLM 生成题难度会漂，没锚点不能称高置信）。

### `next_session` —— 给下游的执行指令

| 字段 | 含义 |
| --- | --- |
| `difficulty_mix` | 下一环节的取题配比，两层 |
| `feedback_style` | 讲解风格 |
| `item_count` | 建议题量 |
| `focus_abilities` | 按薄弱排序的能力维度 |
| `focus_units` | 按本次表现排序的重点单元 |
| `exclude_qids` | 诊断已用掉的题。**防重复出题**——教辅题用一道少一道 |

下游拿 `focus_units` + `focus_abilities` + `difficulty_mix` 直接查教辅 JSON 取题，不必再推理。

---

## `ability` 四类维度

由教辅 `type` 字段归并，括号为 Unit 1–2 题量。

| 维度 | 教辅题型 | 题量 |
| --- | --- | --- |
| 词形变化 | 用词适当形式填空、首字母提示、方框选词 | 49 |
| 汉英转换 | 根据汉语意思完成句子、按要求完成句子 | 37 |
| 篇章理解 | 阅读理解、完形填空、任务型阅读、补全对话等 | 56 |
| 语法辨析 | 单项选择、语法填空 | 47 |
| ~~听力~~ | 听力选择 | 30（demo 无音频，排除） |

本版用于画像判定的有效计分点为 189 个（基础夯实 98、能力提升 91）；写作与自主检测也暂不纳入。

**注意**：「能力提升」中的多个计分点常共享同一篇材料，并非彼此独立。基础夯实单题约 30 秒，能力提升通常需整篇 5–8 分钟。20 分钟预算下的现实配置是「基础夯实 6 道单题 + 能力提升 1 篇」——A 类判定压在这一篇上，证据薄，置信度须相应下调。

---

## 本版故意不做的

以下都讨论过，v1 一律不加，避免过度设计：

- **逐题作答日志 `responses[]`** —— 画像交付的是结论，不是过程。原始作答属于会话记录，需要复盘错题的模块自己去查，不该塞进画像
- 每题的 `provenance` / `anchored_by` / `locator` —— 同上，随日志一起移出
- `is_exam_question` —— 27 道中考真题全在「基础夯实」层，不构成独立难度层，v1 不用
- `seconds` —— 慢不等于不会，本版不进分层
- `assisted` 协助标记 —— 摸底期间一律不给提示，不存在「协助完成」的题，该字段无意义
- `bank_total` 覆盖度、`misconception_tally` —— 中间量，能现算就不存
- `is_boundary` 边界带、`engine_version` —— 样本量上来后再说
- 教材知识图谱、跨单元语法回溯 —— 只有 Unit 1–2 时无用武之地

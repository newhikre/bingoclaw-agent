# 上游契约 —— 学习者画像

任务阶段的唯一必需输入是统一状态中 `profile_engine.evaluate` 的输出，下称**画像**。

**本文件是任务阶段消费画像的完整定义。** 运行时读取当前 Skill 内的 `scripts/profile_engine.py` 与统一状态，不依赖任何兄弟 Skill 文件。

---

## 一、上游如何产出画像

当前 Skill 内置 `scripts/profile_engine.py`，三个子命令：

| 命令 | 用途 | 本 skill 是否消费 |
| --- | --- | --- |
| `validate` | 自检 12 道锚题能否回源解析 | 否 |
| `anchors --units <U> --count <N>` | 挑锚题给学生做诊断 | 否 |
| `evaluate --input <session.json>` | 判分并产出画像 | **是，唯一输入** |

三个底层命令只往 stdout 写 JSON；推荐由 `cycle_engine.py diagnose` 调用并写入统一状态。

异常时输出 `{"ok": false, "error": "<消息>"}` 并以退出码 2 结束；`validate` 自检不通过时退出码 1。**拿到 `ok: false` 的对象时不要把它当画像解析**，这是错误信封，没有 `strategy` 字段。

### 文件名约定

上游不定义文件名。本链路的约定如下，仅为约定：

| 文件 | 内容 | 谁写 |
| --- | --- | --- |
| `session.json` | 诊断会话，学生的原始作答 | 当前闭环状态 |
| `profile.json` | `evaluate` 输出，即画像 | `cycle_engine.py` |
| `task_pack.json` | 当前轮任务包 | 当前 Skill |

调用方直接把画像对象传进来（而不是给路径）时同样成立，本 skill 不关心它从哪来。

---

## 二、画像完整样例

```json
{
  "session_id": "bm-20260806-0193",
  "generated_at": "2026-08-06T15:20:00+08:00",

  "scope": { "units": ["Unit 1", "Unit 2"] },

  "profile": {
    "available_minutes": 20,
    "feedback_preference": "先给思路"
  },

  "mastery": {
    "基础夯实": 66.7,
    "能力提升": 50.0,
    "by_ability": { "词形变化": 100.0, "汉英转换": 50.0, "语法辨析": 0.0, "篇章理解": 50.0 }
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
    "reason": "基础夯实 66.7%、能力提升 50.0%，基础可用但提升不足"
  },

  "next_session": {
    "difficulty_mix": { "基础夯实": 60, "能力提升": 40 },
    "feedback_style": "先给思路",
    "item_count": 6,
    "focus_abilities": ["语法辨析", "汉英转换"],
    "focus_units": ["Unit 2", "Unit 1"],
    "exclude_qids": ["u1-method-by", "gen-u1-by-03", "u2-clause-that"]
  }
}
```

---

## 三、逐字段定义

「恒有」指 `evaluate` 正常返回时该键一定存在，值仍可能为 `null` 或空数组。

### 顶层

| 字段 | 类型 | 恒有 | 语义 | 本 skill 的用途 |
| --- | --- | --- | --- | --- |
| `session_id` | str \| null | 是 | 会话 ID，**原样透传自输入会话，上游不生成** | 写进任务包同名字段，贯穿链路 |
| `generated_at` | str \| null | 是 | 画像生成时间，同样是透传 | 判断画像是否过期，仅参考 |

### `scope`

| 字段 | 类型 | 恒有 | 语义 | 用途 |
| --- | --- | --- | --- | --- |
| `scope.units` | str[] | 是 | 本次评估覆盖的单元，取值形如 `"Unit 1"` | 选题范围的**外边界**，任何情况下不得越出 |

`scope.units` 只包含学生确认学过的单元。没测过的单元不在里面，也不允许出题。

### `profile` —— 自报信息，不参与打分

| 字段 | 类型 | 恒有 | 语义 | 用途 |
| --- | --- | --- | --- | --- |
| `profile.available_minutes` | int | 是 | 单次可用分钟数，缺省 20 | 校验 `item_count` 是否合理 |
| `profile.feedback_preference` | str | 是 | 只有两种取值：`先给思路` / `直接给答案`，缺省 `先给思路` | 讲解风格分支 |

### `mastery` —— 掌握程度

| 字段 | 类型 | 恒有 | 语义 | 用途 |
| --- | --- | --- | --- | --- |
| `mastery.基础夯实` | float \| **null** | 是 | 该难度层正确率百分比，一位小数 | 写 rationale |
| `mastery.能力提升` | float \| **null** | 是 | 同上 | 同上 |
| `mastery.by_ability` | {str: float\|null} | 是 | 四类能力维度各自的正确率 | 候选池优先级、首题挑选 |

**null 的含义是"这一层/这一维一题没测到"，不是 0 分。** 两者的处置完全相反：null 要回避判断，0 要重点补。

`by_ability` **只包含实际测到的维度**，四维不齐是常态。键名只会是下列四个之一。

### `characteristics` —— 对学生的判断

| 字段 | 类型 | 恒有 | 语义 | 用途 |
| --- | --- | --- | --- | --- |
| `strengths` | str[] | 是 | 正确率 ≥80% 的维度，按正确率降序 | 跳过确认性刷题 |
| `weaknesses` | str[] | 是 | 正确率 <60% 的维度，按正确率升序 | 与 `focus_abilities` 同源 |
| `error_pattern` | str \| **null** | 是 | `知识型` / `粗心型` / `思路型`；全对时为 `null` | 决定选题侧重与话术 |
| `misconceptions` | str[] | 是 | 具体误解的人话表述，按出现次数降序 | 锚点细化 |
| `evidence` | {item_count:int, correct:int} | 是 | 有效作答数与做对数 | 写 rationale、说「8 题对 5 题」 |

**`misconceptions` 绝大多数时候是空数组。** 它只有在诊断里用了 LLM 生成题、且该题带 `misconception_map`、且学生答错时才有值；教辅锚题永远不产生它。空是常态，不是数据异常。

### `strategy` —— 层级判定结果

| 字段 | 类型 | 恒有 | 语义 | 用途 |
| --- | --- | --- | --- | --- |
| `code` | `"A"` \| `"B"` \| `"C"` | 是 | 层级 | **直接作为本 skill 的层级，不重判** |
| `name` | str | 是 | `拓展挑战` / `巩固提升` / `稳固基础`，与 code 一一对应 | 对孩子的说法 |
| `confidence` | `"high"` \| `"medium"` \| `"low"` | 是 | 证据强度 | 决定是否降档取题 |
| `reason` | str | 是 | 一句话判定理由，含具体百分比 | 原样写进 `tier.rationale` |

`code` 是唯一不允许缺失、也不允许本 skill 自行推导的字段，理由见第六节。

### `next_session` —— 上游给下游的执行指令

| 字段 | 类型 | 恒有 | 语义 | 用途 |
| --- | --- | --- | --- | --- |
| `difficulty_mix` | {基础夯实:int, 能力提升:int} | 是 | 两档配比，百分比，和为 100 | 换算题位 |
| `feedback_style` | str | 是 | 讲解风格，**取值空间有两套**，见第六节 | 话术风格 |
| `item_count` | int | 是 | 建议题位数，恒在 4–8 | 本次题量 |
| `focus_abilities` | str[] | 是 | 按薄弱排序的能力维度 | **选题锚点** |
| `focus_units` | str[] | 是 | 按本次表现排序的重点单元 | 候选池优先范围 |
| `exclude_qids` | str[] | 是 | 诊断已消耗的题 id | 去重，必须排除 |

---

## 四、枚举值

| 枚举 | 全部取值 |
| --- | --- |
| 层级 `code` | `A` / `B` / `C` |
| 层级 `name` | A=`拓展挑战`、B=`巩固提升`、C=`稳固基础` |
| 置信度 | `high` / `medium` / `low` |
| 错误模式 | `知识型` / `粗心型` / `思路型` / `null` |
| 能力维度 | `词形变化` / `汉英转换` / `语法辨析` / `篇章理解` |
| 难度层 | `基础夯实` / `能力提升` |
| 反馈偏好 | `先给思路` / `直接给答案` |

层级的默认配比与默认风格串（`feedback_preference` 缺席时 `feedback_style` 取这一列）：

| code | difficulty_mix | 默认 feedback_style |
| --- | --- | --- |
| A | `{基础夯实: 30, 能力提升: 70}` | `启发式，以追问和变式为主` |
| B | `{基础夯实: 60, 能力提升: 40}` | `先给思路再分步展开` |
| C | `{基础夯实: 85, 能力提升: 15}` | `概念和例题优先，陪伴式反馈` |

---

## 五、上游的计算口径

写在这里是为了让本 skill 能向老师解释画像里的数字**从哪来**，不是让本 skill 重算。**任何情况下都不要用这些规则复算层级。**

- **层级**：基础夯实 <60% → C；基础夯实 ≥80% 且能力提升 ≥60% → A；其余 → B。无有效作答时强制 B。
- **置信度**：有效作答 ≥8 题且两个难度层都测到 → high；≥4 题且两层都测到 → medium；否则 low。整套一道锚题都没用时，high 降为 medium。
- **`item_count`**：`max(4, min(8, available_minutes // 3))`。
- **`difficulty_mix`**：按 code 查上表，与作答表现无关。
- **`strengths` / `weaknesses`**：正确率 ≥80% 进 strengths，<60% 进 weaknesses，中间地带两边都不进。
- **`focus_abilities`**：等于 `weaknesses`；weaknesses 为空时退化为正确率最低的两维。
- **`focus_units`**：**只列存在错题的单元**，按正确率升序。
- **`error_pattern`**：同一误解重复 ≥2 次 → 知识型；基础夯实正确率低于能力提升 → 粗心型；基础夯实 ≥80% 且能力提升 <60% → 思路型；其余按同维度错题数判知识型或粗心型。
- **`exclude_qids`**：本次诊断的全部有效作答 qid，含教辅锚题 id 与 LLM 生成题 id。

---

## 六、边界情况 —— 必须处理

这些不是异常，是常规输出的合法形态。逐条都要有确定行为。

| # | 情况 | 何时发生 | 本 skill 的处理 |
| --- | --- | --- | --- |
| 1 | `focus_units` 是**空数组** | 学生全对，没有任何单元有错题 | 回退到 `scope.units` 全部。**不得用空数组去筛候选池**，那会筛出零结果 |
| 2 | `focus_abilities` 是空数组 | 一题都没测到 | 回退到 `by_ability` 里正确率最低的两维；`by_ability` 也空时锚点取 `词形变化`，并记 `data_gaps` |
| 3 | `misconceptions` 是空数组 | **常态**，诊断没用生成题就必然为空 | 锚点停在能力维度层面，不细化；不要因此判定数据缺失 |
| 4 | `mastery.基础夯实` 或 `能力提升` 为 `null` | 该难度层一题没测到 | 不写进 rationale，不当 0 处理；能力提升为 null 时篇章题定位改为拉伸 |
| 5 | `by_ability` 缺维度 | 该维度没测到 | 缺的维度按"未知"处理，排优先级时排在已知维度之后，不排在最前 |
| 6 | `error_pattern` 为 `null` | 全对 | 选题不做侧重调整，话术不提错因归类 |
| 7 | `session_id` 或 `generated_at` 为 `null` | 上游调用方没在会话里填 | 自行生成一个 `local-<时间戳>` 写进任务包，并在 `data_gaps` 注明是本 skill 补的 |
| 8 | `feedback_style` 取值不在预期内 | 见下 | 见下 |
| 9 | `evidence.item_count` 为 0 | 无有效作答，上游强制判 B | 层级仍用 B，但整包按最保守配比走，`data_gaps` 写明"画像无作答证据" |
| 10 | 顶层是 `{"ok": false, "error": ...}` | 诊断执行失败 | **不是画像**。停止并在内部修复，不把 error 原样发给学生 |
| 11 | 缺 `strategy.code`、有效证据或合法范围 | 传入的不是可用画像 | 拒绝继续，回到 `diagnose`；任务阶段不自行判层 |

### 第 8 条展开：`feedback_style` 的两套取值空间

上游的取值逻辑是「有 `feedback_preference` 就用它，否则用层级默认串」，因此这个字段会出现两类完全不同的值：

| 来源 | 可能取值 |
| --- | --- |
| 学生自报 | `先给思路` / `直接给答案` |
| 层级默认 | `启发式，以追问和变式为主` / `先给思路再分步展开` / `概念和例题优先，陪伴式反馈` |

本 skill 做风格分支时**两套都要认**，判定规则如下：

- 值里含「直接给答案」→ 走直给分支（各层手册里都写了这一分支怎么做）
- 其余一律走本层默认的讲解风格

不要对这个字段做精确字符串相等匹配。

---

## 七、入口校验清单

收到画像后、动手选题前，按顺序过一遍：

1. 顶层没有 `ok: false` —— 否则按第 10 条停止
2. `strategy.code` 存在且属于 `A`/`B`/`C` —— 否则按第 11 条停止
3. `scope.units` 非空 —— 空则无从出题，停止并索取
4. `next_session` 存在 —— 缺失则整块按各层手册的兜底值补，并记 `data_gaps`
5. `focus_units` ⊆ `scope.units` —— 不满足时以 `scope.units` 为准
6. `focus_units`、`focus_abilities` 空数组按第 1、2 条回退
7. `item_count` 在 4–8 之间 —— 越界则夹到区间内并记 `data_gaps`
8. `difficulty_mix` 两键之和为 100 —— 不满足则改用本层默认配比

前 3 条不通过就停止，不产出任务包。第 4 条起都是可降级项，降级必须记录。

---

## 八、最小可用画像

下面这份是能跑通本 skill 的下限。缺的字段全部走兜底，`data_gaps` 会记满，但不阻断。

```json
{
  "scope": { "units": ["Unit 1"] },
  "strategy": { "code": "C", "name": "稳固基础", "confidence": "low", "reason": "" }
}
```

对应行为：题量按 6，配比按 C 层默认 `85/15`，锚点取 `词形变化`，风格用 C 层默认，`exclude_qids` 视为空，`data_gaps` 记录以上全部。

---

## 九、兼容规则

任务生成本身只读画像；报告成功后由 `cycle_engine.py` 按 `state-contract.md` 自动回写观测与本次消耗题目，且绝不自动改变 `strategy`。

**未知字段一律忽略。** 上游后续版本新增字段不应导致本 skill 报错。

**已知的字段收缩要容忍。** 上游文档提到过 `responses[]`、`provenance`、`seconds`、`is_boundary` 等字段"本版故意不做"，本 skill 不得依赖它们；若某天出现，按未知字段忽略。

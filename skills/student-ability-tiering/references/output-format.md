# 输出格式

产出物有两样：任务包供智能体调用，引导话术面向学生。

任务包的字段命名与上游画像保持一致（`part` / `ability` / `unit` / `period` / `qid`）。

## 一、任务包结构

```json
{
  "session_id": "bm-20260806-0193",
  "generated_at": "2026-08-06T16:05:00+08:00",
  "workbook": "《2026 初中53同步》人教版 九年级全一册",
  "bank_available": true,

  "tier": {
    "level": "B",
    "name": "巩固提升",
    "source": "profile.strategy",
    "confidence": "medium",
    "rationale": "基础夯实 66.7%、能力提升 50.0%，基础可用但提升不足；最弱维度为语法辨析",
    "downgraded_mix": null
  },

  "session": {
    "anchor": "介词 by 后的动词用 -ing 形式",
    "anchor_ability": "语法辨析",
    "from_misconception": "by 后的动名词误作不定式",
    "units": ["Unit 1"],
    "difficulty_mix": { "基础夯实": 60, "能力提升": 40 },
    "item_count": 6,
    "layout": { "基础夯实": 4, "能力提升篇数": 1 },
    "estimated_minutes": 13,
    "teaching_style": "先给思路再分步展开"
  },

  "items": [
    {
      "seq": 1,
      "source": "workbook",
      "qid": null,
      "locator": {
        "unit": "Unit 1",
        "period": "第3课时",
        "exercise_no": "II",
        "question_no": 6
      },
      "section": "Section A (Grammar Focus)",
      "part": "基础夯实",
      "type": "根据汉语意思完成句子，每空一词",
      "ability": "汉英转换",
      "knowledge_point": "介词 with 表工具",
      "stem": "那个小男孩正在用他的左手写字。The little boy is writing ______ ______ ______ ______.",
      "role": "开场建信心，同时把 by 与 with 的分工先摆出来",
      "acceptable_answers": ["with his left hand"],
      "hint_ladder": [
        "'用某个工具做事'，介词不是 by",
        "with 后面直接跟这个工具，不用改形式"
      ]
    },
    {
      "seq": 2,
      "source": "workbook",
      "qid": null,
      "locator": {
        "unit": "Unit 1",
        "period": "第3课时",
        "exercise_no": "II",
        "question_no": 7
      },
      "section": "Section A (Grammar Focus)",
      "part": "基础夯实",
      "type": "根据汉语意思完成句子，每空一词",
      "ability": "汉英转换",
      "knowledge_point": "介词 by 后的动词用 -ing 形式",
      "stem": "我的老师建议我通过看英文电视节目学英语。My teacher advised me to learn English ______ ______ ______ ______ ______.",
      "role": "锚点首次出现，中译英外壳",
      "acceptable_answers": ["by watching English TV programs"],
      "hint_ladder": [
        "'通过……的方式'用哪个介词？",
        "这个介词后面跟动作时要变形，变成哪种形式？"
      ]
    },
    {
      "seq": 3,
      "source": "variant",
      "derived_from": {
        "unit": "Unit 1",
        "period": "第3课时",
        "exercise_no": "II",
        "question_no": 7
      },
      "rule": "改情境",
      "purpose": "验证真会",
      "part": "基础夯实",
      "type": "用括号内所给词的适当形式填空",
      "ability": "词形变化",
      "knowledge_point": "介词 by 后的动词用 -ing 形式",
      "stem": "—How did Lily memorize so many new words? —She did it by ______ (make) word cards.",
      "acceptable_answers": ["making"],
      "self_check_passed": true,
      "in_scope": true,
      "hint_ladder": ["空前面是 by，by 后面的动词一律加 -ing"]
    },
    {
      "seq": 5,
      "source": "workbook",
      "qid": null,
      "locator": {
        "unit": "Unit 1",
        "period": "第3课时",
        "exercise_no": "III",
        "question_no": [8, 9, 10]
      },
      "section": "Section A (Grammar Focus)",
      "part": "能力提升",
      "type": "任务型阅读",
      "ability": "篇章理解",
      "knowledge_point": "根据需求匹配社团介绍",
      "role": "拉伸，验证锚点在整篇语料里还认不认得出来",
      "acceptable_answers": {
        "8": ["B", "B. The Creative Writing Club"],
        "9": ["A", "A. The Language Club"],
        "10": ["D", "D. The Science Club"]
      },
      "hint_ladder": [
        "先看每个人最想要什么，画出那个关键词",
        "再回社团介绍里找同义说法，不要找一模一样的词"
      ]
    }
  ],

  "branches": {
    "连续做对 3 题": "跳过剩余基础夯实题，直接进入能力提升篇",
    "连续做错 2 题": "停止推进，切换为降阶变式，回到锚点最简单的一道原题",
    "提前做完": "从候选池追加 1 道同考点基础夯实题，不追加篇章",
    "情绪信号转差": "主动收尾，肯定已完成部分"
  },

  "next_session": {
    "exclude_qids": [
      "u1-method-by",
      "u2-shot-past",
      "Unit 1/第3课时/II/6",
      "Unit 1/第3课时/II/7",
      "Unit 1/第3课时/III/8-10"
    ]
  },

  "data_gaps": [
    "题库无页码字段，题目只能定位到单元与课时",
    "非锚题无稳定 qid，跨会话去重依赖四元组定位"
  ],

  "out_of_scope": ["层级更新与升降层", "学习报告"]
}
```

样例省略了 `seq: 4`（同考点的第三种外壳，一道 `单项选择`），因此 `items` 里 seq 从 3 跳到 5。

### 字段来源

画像侧字段的完整定义与取值范围见 `profile-contract.md`，这里只写映射关系。

| 任务包字段                                                 | 取自                                                                                                                        |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `session_id`                                               | 画像 `session_id`，贯穿四个环节，不新建；**画像里为 null 时**自行生成 `local-<时间戳>` 并在 `data_gaps` 注明是本 skill 补的 |
| `bank_available`                                           | 题库路径解析的结果，见 `bank-contract.md` 第五节                                                                            |
| `tier.level` / `name` / `confidence` / `rationale`         | 画像 `strategy.code` / `name` / `confidence` / `reason`                                                                     |
| `tier.source`                                              | 固定为 `profile.strategy`，标明层级不是本环节算的                                                                           |
| `session.difficulty_mix` / `item_count` / `teaching_style` | 画像 `next_session` 同名字段                                                                                                |
| `session.anchor_ability`                                   | 画像 `next_session.focus_abilities[0]`                                                                                      |
| `session.from_misconception`                               | 画像 `characteristics.misconceptions[0]`                                                                                    |
| `session.units`                                            | 画像 `next_session.focus_units`；**空数组时取 `scope.units`**                                                               |
| `items[].part` / `type` / `ability`                        | 教辅题库原字段，不重新标注                                                                                                  |
| `next_session.exclude_qids`                                | 画像原有的 `exclude_qids` **加上**本次用掉的题                                                                              |

`tier.downgraded_mix` 只在 `confidence` 为 `low` 触发降档时填写，写入实际执行的配比；未降档时为 `null`。

### 降级模式下的差异

题库读不到时（`bank_available: false`），任务包结构不变，以下几处按此填：

| 字段                                       | 降级模式下                                                                         |
| ------------------------------------------ | ---------------------------------------------------------------------------------- |
| `bank_available`                           | `false`                                                                            |
| `workbook`                                 | 保留书名，它来自画像链路而非题库                                                   |
| `items[].source`                           | 一律 `generated`（不是 `variant`——没有可派生的原题）                               |
| `items[].qid` / `locator` / `derived_from` | **一律 `null`**，不编造课时与题号                                                  |
| `items[].part` / `ability`                 | 由本 skill 按配比与锚点指定，不再是"题库原字段"                                    |
| `items[].self_check_passed` / `in_scope`   | 每道题都必须为 `true`，见 `variant-generation.md` 第四节                           |
| `data_gaps`                                | 必须含一条「题库不可用，全部题目为生成题，未经人工质检」，并写明路径解析失败的原因 |

话术同步受限：**不出现任何课时与题号**，出题时改说「这道题考的是……」。`tier`、`session` 两块完全不受影响，它们只依赖画像。

### 必填字段

必填项为 `tier.level`、`tier.source`、`tier.rationale`、`session.difficulty_mix`、`session.layout`、`items`（每道题至少包含 `source`、`part`、`ability`、`acceptable_answers`），以及 `branches` 中的「连续做错 2 题」分支。

其余字段没有对应内容时留空或省略，不编造。**`locator` 尤其如此**——课时或题号写错会让学生翻到一道完全不相干的题，这是最容易被家长发现也最伤信任的错误。题库里没有的信息（页码就是其中之一）一律不写。

### 容易写错的地方

**`acceptable_answers` 必须是数组或按小题编号的对象，不能是单个字符串。** 英语填空常有多解：`if` 与 `whether` 在宾语从句里通常都成立，`loud` 与 `loudly` 在不同句子里各自成立，选择题的 `B` 与 `B. by` 都应接受。漏写会把对的判成错的，这是本环节最高频的线上事故。

原题的答案来自题库 `exercises[].answer`，按 `question_no` 取，不自己重算。答案串里的斜杠与分号有确定含义（可替换写法、分空填写），展开成 `acceptable_answers` 的规则见 `bank-contract.md` 第二节——**不要只取斜杠左边那个**。变式题的答案来自执行者自己的解算，且 `self_check_passed` 与 `in_scope` 都需要为 `true`。

`hint_ladder` 是提示而非答案，最后一级也不直接给出结果。阶梯的含蓄程度随本层的讲解风格调整：A 层三级、B 层两级、C 层可以只有一级但要拆得很细。

`role` 用于说明这道题为什么在这里，写不出来的题不进入任务包。

一篇「能力提升」材料在 `items` 里是一条记录，`question_no` 写成数组，`seq` 只占一个位置，但按 2 个题位计入 `layout`。

## 二、引导话术

话术按本层的讲解风格撰写，模板在各层手册里，至少覆盖五个场景。

开场说明今天学什么、为什么是这个、需要多久。

出题指明单元与课时或直接给出变式题干，说明考点，给出时间。

做对给出反馈并说明下一步是否出变式。

做错按本层风格处理，包含提示阶梯的第一级。

收尾说明今日完成情况，并给出一句具体的进步证据。

话术写成可直接使用的完整句子。「此处表扬学生」这类占位说明不满足要求，下游需要的是能直接说出口的话。

**结构化字段不出现在话术里。** 不说「你的层级是 B」「置信度 medium」「focus_abilities 是语法辨析」，说「我建议走巩固提升这条路」「这次测得比较浅，先按这个来」「你卡在 by 后面跟什么形式上」。

**层级代号 `A` / `B` / `C` 一律不出口，第三人称的类别引用同样禁止。** 「考试里 A 层丢分最多的就是这种」「B 层的学生都栽在这儿」这类说法不比「你的层级是 B」轻——它把分层暴露成了一套学生能对号入座的等级制，学生会立刻推断自己被划进哪一格，以及上下还有哪几格。**层级名（拓展挑战 / 巩固提升 / 稳固基础）可以说**，说的时候是一条路线而不是一个档次：「我建议走拓展挑战这条路」成立，「你是拓展挑战层」不成立。

**不拿别的学生做参照。** 「大部分人这道都能做对」「这个分数在班里算低的」既没有数据支撑，也不产生任何可执行的下一步。要制造紧迫感就落到题本身——「这道题的坑在 loud 和 loudly，看走眼一次就丢一分」，学生知道该盯哪儿；说「A 层丢分最多」他只知道自己危险，不知道危险在哪。

话术的措辞受本层约束，下笔前回看一遍所在层手册的措辞禁忌。

## 三、判定说明

最后附两三句说明，讲清楚层级来自画像的哪一条判定、为什么选这些题。这段面向产品和老师，需要经得起追问。

触发了置信度降档时，在这里明确写出，不藏在 JSON 里等别人自己发现。

上面那份样例对应的说明：

> 层级不是本环节判的，画像给的是 B（基础夯实 66.7%、能力提升 50.0%）。锚点取的是画像里的第一条误解「by 后的动名词误作不定式」，前四道基础题都围绕它换外壳——中译英一道、括号填空一道、单选一道——用来确认这一条是真不会还是当时看漏了。置信度是 medium，最后那篇任务型阅读按拉伸处理，做不出来不计为失败。

触发降档时的写法：

> 画像判的是 A，但置信度是 low——摸底只答了 5 题且能力提升只测到一篇，A 的判定基本压在那一篇上。本次难度配比按 B 层执行（1 篇 + 4 道单题），讲解风格仍用 A 层的启发式。下一次摸底补足能力提升的题量后再按 A 的配比走。

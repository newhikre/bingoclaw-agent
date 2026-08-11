# 学习报告

任务包发出去、题讲完之后的收尾。产出物有三样：**报告 JSON**（给智能体与家长工作台读）、**收尾话术**（面向孩子）、**家长同步**（面向家长）。

判分与统计一律由 `scripts/report_engine.py` 算，**不手算、不估算**。同一份作答必须得到同一份报告——这是它值得存在的唯一理由。

---

## 零、会话文件：每轮推完、做完都追加进去

**一次学习对应一个会话文件 `session-<名字>.json`**，`<名字>` 用孩子的名字或画像的 `session_id`，一个孩子一次学习一份。**每推一轮往里追加一条，这一轮做完再把作答记录回填到同一条**，报告直接读这份文件，不用调用方自己攒一堆散文件、也不用记住谁配谁。

```bash
# 第 1 轮：任务包出好、发给孩子的同时追加一条
python scripts/report_engine.py append --session session-小宇.json --pack round1_pack.json

# 这一轮做完，回填作答记录（默认填最后一个还没记录的轮次）
python scripts/report_engine.py append --session session-小宇.json --log round1_log.json

# 又推了一轮，重复上面两步；最后出报告
python scripts/report_engine.py append --session session-小宇.json --pack round2_pack.json
python scripts/report_engine.py append --session session-小宇.json --log round2_log.json
python scripts/report_engine.py report --session session-小宇.json --profile profile.json
```

```json
{
  "session_id": "bm-20260806-0193",
  "workbook": "《2026 初中53同步》人教版 九年级全一册",
  "rounds": [
    {
      "round": 1,
      "pushed_at": "2026-08-06T16:05:00+08:00",
      "completed_at": "2026-08-06T16:20:00+08:00",
      "task_pack": { "……第 1 轮的整份任务包": "" },
      "log": { "……第 1 轮的授课记录": "" }
    },
    { "round": 2, "pushed_at": "2026-08-06T16:22:00+08:00", "task_pack": {}, "log": null }
  ]
}
```

| 规矩 | 为什么 |
| --- | --- |
| **用 `append` 写，不要自己整份重写** | 命令是读进来改完再写回，旧轮次原样保留；整份重写最容易把第 1 轮弄丢 |
| 推题时先 `--pack` 追加，做完再 `--log` 回填 | 没做完的轮次 `log` 为 `null`，报告会跳过它并在 `data_gaps` 记一条，中途收尾也出得了报告 |
| 回填默认落在最后一个 `log` 为 `null` 的轮次 | 要改已经填过的轮次得显式 `--round n`，避免手滑覆盖 |
| 文件不存在时 `append --pack` 自建 | `session_id` 取任务包的，缺了就生成 `local-<时间戳>` |

**报告只认做完的轮次。** 只推了题没有记录的那一轮不进正确率，但会在 `data_gaps` 里留痕——它是「推了没做」，不是「不存在」。

会话文件是本 skill 唯一会写盘的东西，**画像仍然只读不写**。

## 一、输入：任务包 + 授课记录，一次推题一对

**一次推题是 n 道题（一份任务包），推了几次就有几份任务包、几份授课记录，一一对应。** 一次学习里孩子做完一批还有时间、或者错得多需要补一批，都会产生第二份任务包——报告要把每一次单独记一条，再给一份合起来的汇总。

用会话文件的话配对由 `append` 管住；不落盘的一次性调用可以直接传成对的 `--pack` / `--log`，这时**谁跟谁配对由传参顺序决定**，别错位。

任务包只有题和标准答案，没有孩子答了什么。**授课记录是执行任务包的那个智能体边讲边记的**，报告环节不重建它，记漏了就是漏了。下面是**一次推题**对应的那一份记录：

```json
{
  "minutes_actual": 15,
  "ended_early": false,
  "responses": [
    {
      "seq": 1,
      "response": "With his left hand.",
      "hints_used": 0,
      "seconds": 35
    },
    {
      "seq": 2,
      "response": "by watch English TV programs",
      "hints_used": 1,
      "seconds": 90
    },
    {
      "seq": 4,
      "response": { "8": "B", "9": "C", "10": "D" },
      "hints_used": 0,
      "seconds": 300
    }
  ],
  "data_gaps": []
}
```

| 字段                     | 类型                 | 必填       | 语义                                                                                         |
| ------------------------ | -------------------- | ---------- | -------------------------------------------------------------------------------------------- |
| `responses[].seq`        | int                  | 是         | 对应任务包 `items[].seq`，**不在任务包里的 seq 直接报错**，不容忍改题                        |
| `responses[].response`   | str \| {小题号: str} | 是         | 学生原话，**原样记录，不要替他改拼写、补大小写**——判分脚本自己会归一化，替他改过就看不出粗心 |
| `responses[].hints_used` | int                  | 否，缺省 0 | 用掉几级提示。这一条决定这道题算不算独立掌握                                                 |
| `responses[].seconds`    | int                  | 否         | 作答秒数，只进报告不进判定                                                                   |
| `minutes_actual`         | int                  | 否         | 实际用时，与 `session.estimated_minutes` 对照                                                |
| `ended_early`            | bool                 | 否         | 提前收尾（情绪转差、时间不够）                                                               |
| `data_gaps`              | str[]                | 否         | 记录本身的缺口，原样并进报告                                                                 |

**没作答的题不写进 `responses`。** 写一个空字符串和不写是一回事（都判「未作答」），但不写更省事。未作答的题不进正确率分母，只进 `follow_up`。

**`seq` 只在自己那一轮里唯一**，第 2 轮的 `seq: 1` 是另一道题，不会跟第 1 轮的串在一起。同一份记录里同一个 `seq` 出现两次的话，后一条覆盖前一条——**重做同一道题不算一轮**，一轮的界线是「又推了一份任务包」。

篇章材料的 `response` 是**按小题号的对象**，键要与任务包 `acceptable_answers` 的键对上；只答了一半就只写答了的那部分。

---

## 二、判分口径

| 情况               | `status`     | 计不计独立掌握             |
| ------------------ | ------------ | -------------------------- |
| 全对、没用提示     | `正确`       | 计                         |
| 全对、用了提示     | `提示后正确` | **不计**                   |
| 篇章材料部分小题对 | `部分正确`   | 对的小题计，前提是没用提示 |
| 全错               | `错误`       | 不计                       |
| 没写               | `未作答`     | 不进分母                   |

**用了提示就不算独立掌握**，整道题的得分点都归到 `assisted`。会做和被扶着做不是一回事，这一条如果放松，掌握度会一路虚高，下一轮直接给孩子推超纲的题。报告同时给两个数：`accuracy_independent`（写进画像的那个）与 `accuracy_with_hints`（对孩子说的那个）。

**一篇「能力提升」材料按小题拆成多个计分点**，不是一个。选题时它按 2 个题位计（见 SKILL.md 的配比换算），判分时按实际小题数计，两套口径各管各的。

**答案归一化与上游 `profile_engine` 同口径**：忽略大小写、首尾标点、多余空格；选择题 `B` 与 `B. by watching` 都接受；`loud / loudly` 这类斜杠多解全展开。所以孩子写 `With his left hand.` 判对，不算他多写句号。

---

## 三、输出：报告 JSON

```bash
# 常规：读会话文件，里面做完的轮次全部计入
python scripts/report_engine.py report --session session-小宇.json --profile profile.json

# 没落盘时：--pack 与 --log 成对传，推了两次就各传两遍，按先后顺序配对
python scripts/report_engine.py report \
  --pack round1_pack.json --log round1_log.json \
  --pack round2_pack.json --log round2_log.json \
  --profile profile.json
```

`--session` 与 `--pack/--log` 二选一，混着传报错；成对传时两者份数不等也报错，不猜谁配谁。`--profile` 可省，省了就没有掌握度变化量，`data_gaps` 会记一条。stdout 输出 JSON，异常时输出 `{"ok": false, "error": "..."}` 并以退出码 2 结束，与上游脚本一致。

```json
{
  "report_id": "rep-20260806-1642",
  "session_id": "bm-20260806-0193",
  "generated_at": "2026-08-06T16:42:00+08:00",
  "workbook": "《2026 初中53同步》人教版 九年级全一册",
  "bank_available": true,

  "tier": { "level": "B", "name": "巩固提升", "source": "task_pack.tier" },

  "rounds_count": 2,
  "rounds": [
    {
      "round": 1,
      "session_id": "bm-20260806-0193",
      "tier_level": "B",
      "anchor": "介词 by 后的动词用 -ing 形式",
      "units": ["Unit 1"],
      "bank_available": true,
      "summary": {
        "items_planned": 4,
        "items_attempted": 4,
        "points_total": 6,
        "points_independent": 3,
        "points_assisted": 0,
        "accuracy_independent": 50.0,
        "accuracy_with_hints": 50.0,
        "minutes_estimated": 13,
        "minutes_actual": 15,
        "ended_early": false
      },
      "by_part": { "基础夯实": { "points": 3, "independent": 33.3, "with_hints": 33.3 } },
      "by_ability": { "语法辨析": { "points": 1, "independent": 0.0, "with_hints": 0.0 } },
      "items": ["……见下方 items 的字段说明"],
      "follow_up": ["……本轮的错题，每条带 round"],
      "exclude_qids": ["Unit 1/第3课时/II/6", "Unit 1/第3课时/II/7"],
      "data_gaps": []
    },
    {
      "round": 2,
      "anchor": "介词 by 后的动词用 -ing 形式",
      "units": ["Unit 2"],
      "summary": {
        "items_planned": 2,
        "items_attempted": 2,
        "points_total": 2,
        "points_independent": 2,
        "accuracy_independent": 100.0,
        "minutes_actual": 5,
        "ended_early": false
      },
      "items": ["……第 2 轮的题，seq 从 1 重新起"],
      "follow_up": [],
      "exclude_qids": ["Unit 2/第1课时/I/3"]
    }
  ],

  "session_summary": {
    "rounds_count": 2,
    "anchors": ["介词 by 后的动词用 -ing 形式", "介词 by 后的动词用 -ing 形式"],
    "units": ["Unit 1", "Unit 2"],
    "items_planned": 6,
    "items_attempted": 6,
    "points_total": 8,
    "points_independent": 5,
    "points_assisted": 0,
    "accuracy_independent": 62.5,
    "accuracy_with_hints": 62.5,
    "minutes_actual": 20,
    "ended_early": false
  },

  "by_part": {
    "基础夯实": {
      "points": 3,
      "independent_correct": 1,
      "assisted_correct": 0,
      "independent": 33.3,
      "with_hints": 33.3
    },
    "能力提升": {
      "points": 3,
      "independent_correct": 2,
      "assisted_correct": 0,
      "independent": 66.7,
      "with_hints": 66.7
    }
  },

  "by_ability": {
    "语法辨析": { "points": 1, "independent": 0.0, "with_hints": 0.0 }
  },

  "follow_up": [
    {
      "round": 1,
      "seq": 2,
      "reason": "做错",
      "ability": "语法辨析",
      "knowledge_point": "介词 by 后的动词用 -ing 形式",
      "locator": {
        "unit": "Unit 1",
        "period": "第3课时",
        "exercise_no": "II",
        "question_no": 7
      }
    }
  ],

  "mastery_delta": {
    "基础夯实": { "before": 66.7, "after": 33.3, "delta": -33.4 },
    "能力提升": { "before": 50.0, "after": 66.7, "delta": 16.7 }
  },

  "error_pattern": "知识型",

  "tier_recommendation": {
    "current": "B",
    "action": "保持",
    "reason": "独立正确率 50.0%，本层配比合适",
    "confidence": "low",
    "decided_by": "build-learner-profile evaluate"
  },

  "profile_patch": {
    "exclude_qids": [
      "u1-method-by",
      "Unit 1/第3课时/II/6",
      "Unit 1/第3课时/II/7"
    ],
    "mastery_observed": { "基础夯实": 33.3, "能力提升": 66.7 },
    "by_ability_observed": { "语法辨析": 0.0, "词形变化": 0.0 },
    "focus_abilities_hint": ["词形变化", "语法辨析"],
    "misconceptions_observed": ["介词 by 后的动词用 -ing 形式"],
    "error_pattern": "知识型",
    "apply_note": "调用方负责合并回画像；本 skill 只读不写"
  },

  "data_gaps": [],
  "out_of_scope": [
    "层级判定与升降层（属 build-learner-profile）",
    "跨会话趋势与遗忘曲线复习调度"
  ]
}
```

### 每一轮里的 `items`

一道题一条，字段照抄任务包再补上判分结果：

```json
{
  "seq": 2,
  "source": "workbook",
  "part": "基础夯实",
  "ability": "语法辨析",
  "knowledge_point": "介词 by 后的动词用 -ing 形式",
  "locator": { "unit": "Unit 1", "period": "第3课时", "exercise_no": "II", "question_no": 7 },
  "status": "错误",
  "hints_used": 1,
  "seconds": 90,
  "points_total": 1,
  "points_correct": 0,
  "points_independent": 0,
  "detail": [{ "question_no": null, "response": "by watch English TV programs", "correct": false }]
}
```

`detail` 里是每个计分点的原始作答，篇章材料有几道小题就有几条。

### 四块给下游读的

| 块                                           | 谁读                 | 怎么用                                        |
| -------------------------------------------- | -------------------- | --------------------------------------------- |
| `rounds[]`                                   | 老师、复盘           | **推了几次就有几条**，每条能单独看那一轮的表现 |
| `session_summary` / `by_part` / `by_ability` | 家长工作台、老师     | 全部轮次合起来的终值，直接展示                |
| `follow_up`                                  | 错题本、下一轮       | 每条带 `round` 与 `locator`，翻得回书上那道题 |
| `profile_patch`                              | **调用方，必须处理** | 合并回画像，见下一节                          |

**轮次记录不合并、不去重、不省略。** 第 1 轮做错、第 2 轮的同考点题做对，两条都留着——把它们抹成一个结果就看不出「讲完之后才会」这件事，而这恰恰是判断真会假会的依据。轮次记录也是唯一能回答「哪一轮开始垮的」的东西。

汇总口径：`points_*` 与正确率是**全部轮次合起来算**的（不是各轮正确率的平均），`minutes_actual` 是各轮相加，`units` 是并集，`anchors` 按轮次顺序列出。各轮层级不一致时按第 1 轮归口，并在 `data_gaps` 记一条。

`follow_up` 的 `reason` 只有四种：`做错` / `部分做对` / `用了提示才做对` / `没做`。「用了提示才做对」也进这张表——当时会了不等于明天还会。

### `profile_patch` 是回写用的，不是展示用的

第三段结束时留下的那句「`exclude_qids` 必须由调用方合并回画像」（见 `output-format.md`），落点就在这里。`profile_patch` 把要回写的东西凑齐了，但**本 skill 不写画像**，链路上没有别的环节兜——没人合并就意味着下一轮推同一批题、画像停在摸底那天的数字。

| 补丁字段                                   | 合并到画像的                                                  |
| ------------------------------------------ | ------------------------------------------------------------- |
| `exclude_qids`                             | `next_session.exclude_qids`，**并集**，不是覆盖；多轮时已按顺序合过一次 |
| `mastery_observed` / `by_ability_observed` | 本次实测值，作为 `mastery` 的最新一次观测                     |
| `focus_abilities_hint`                     | 下一轮 `focus_abilities` 的候选，独立正确率 <60% 的维度按升序 |
| `misconceptions_observed`                  | 本次做错的考点，可并入 `characteristics.misconceptions`       |
| `error_pattern`                            | 覆盖 `characteristics.error_pattern`                          |

**`tier_recommendation` 不是层级判定。** 它只是一条建议加它的证据，`decided_by` 明写着层级由 `build-learner-profile evaluate` 定。单次会话最多 8 个计分点，据此升降层比不改更危险，所以 `confidence` 封顶 `medium`，绝不给 `high`。判定规则：独立正确率 ≥85% 且计分点 ≥6 → 建议下轮摸底考察升层；<50% → 考察降层；其余保持。

---

## 四、收尾话术（面向孩子）

方案场景四的四段式，按本层讲解风格写，**说人话，不念 JSON**：

1. **今天做了多少、对了多少** —— 用 `accuracy_with_hints` 那个数，对孩子不必强调「提示不算」
2. **哪个点拿下了** —— 从 `正确` 且属于本次锚点的题里挑一条，要具体到考点，不说「基础不错」
3. **哪个点还欠着** —— 从 `follow_up` 里挑最要紧的一条，带上出处让他在书上圈出来
4. **明天先干什么** —— 一句，落到具体考点，不落到「继续加油」

出处怎么报见 `output-format.md` 话术一节，规则完全一致：`bank_available: false` 时**不出现任何课时与题号**，只说考点。

> 小宇，今天 4 道题，独立做对 2 道 👍
>
> 阅读那篇你抓得挺准，三道对了两道——上次两道全错，这次能从文章里找出同义说法了。
>
> 还欠着的是 by 后面跟什么形式，今天栽了两次：一次写成 by watch，一次写成 to make。同一条规则，两道题都在 Unit 1 第 3 课时第 7 题那一块，书上标一下。
>
> 明天先过这一条，就三分钟，过完我们再往下走。

**不报层级、不报置信度、不念 `error_pattern`**。「知识型」要翻译成「不是你笨，是这条规则没扎住」。

**层级代号 A/B/C 与层级名（拓展挑战 / 巩固提升 / 稳固基础）都不出口**，`tier` 与 `tier_recommendation` 是给系统读的，不进任何一句说给人听的话——尤其不说「你升层了」「下次可能要降层」，那是把一次 6 个计分点的会话结果说成了对孩子的重新定级。要表达进步就落到考点：「阅读这块比上次稳了，三道对了两道」。理由见 `output-format.md` 话术一节。

**`ended_early: true` 时不追问原因，也不表达失望**，就按已完成的部分做总结。

---

## 五、家长同步

方案 3.3 定了家长参与度按层递增，同步的**详略跟着层走，但层级本身不出现在字面上**：

| 层 | 给家长什么 |
| --- | --- |
| A | 一句话结果 + 掌握度变化，不给动作项 |
| B | 结果 + 明天练什么，家长确认进度即可 |
| C | 结果 + 明天练什么 + 一条具体的陪伴动作（陪读三分钟、听他讲一遍规则） |

**给家长的文字里同样不写层级代号与层级名**，也不写「分层」「层级」这套词。家长拿到「孩子是 C 层」只会转头说给孩子听，效果和当面说一样；而且家长会拿它跟别人家孩子比，这个数字经不起比——它是单科单元的一条排课路线，不是综合评价。写详略差异即可，不写差异的来由。

内容全部取自 `session_summary`、`mastery_delta`、`follow_up`，**只讲事实与下一步**。

**不评价孩子。** 「今天不太专心」「态度有点松」这类话一句都不写——这是执行者的主观印象，没有任何字段支撑它，家长转述给孩子就成了一顶帽子。用时超了就说用时超了，题没做完就说做到第几道，让家长自己看事实。

> 今日《53同步》Unit 1 第 3 课时，4 道题独立做对 2 道，用时 15 分钟。
> 阅读理解从 50% 升到 66.7%，是本周第一次提升。
> 还没拿下的是 by 后接动名词，同一条规则今天错了两次，明天会先补这一条。
> 可以做的一件事：让他把这条规则讲给你听一遍，讲得出来就是真会了。

`ended_early: true` 时如实写「今天做到第 3 道收的」，不写原因——原因是猜的。

---

## 六、几条不做的事

- **不重算层级。** 只给建议与证据，层级归 `build-learner-profile`。
- **不手算正确率。** 脚本算完是多少就是多少，不为了让报告好看凑数。
- **不写画像文件。** 只产出 `profile_patch`，合并是调用方的动作。
- **不做跨会话趋势。** 报告只覆盖本次会话，「连续三天下滑」这类结论需要历史数据，本链路没有存。
- **不给孩子看 JSON。**

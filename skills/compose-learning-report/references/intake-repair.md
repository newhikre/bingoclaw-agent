# 作答记录入口修复

`scripts/intake.py` 能修什么、不能修什么，以及 `issues` 逐条怎么处理。

## 一、为什么必须先跑这一步

`report_engine.py` 里配对作答与题目的只有一行：

```python
records = {r.get("seq"): r for r in log.get("responses", [])}
```

三个后果连在一起：

1. 外层键不是 `responses`（写成 `items`、`answers`、或直接是个数组），`log.get("responses", [])` 返回空列表；
2. 每道题拿到 `record=None`，`grade_item` 判成「未作答」；
3. **脚本不报错，正常出报告，所有数字是零。**

这是最危险的一种失败：它长得像一份正常报告。微信通道实测已经踩过——当时看到「全未作答」，误判成 qid 与 locator 的映射问题，反复改任务包无果，最后手工核对凑了一份报告交出去，违反了「以脚本结果为准」。

真正的映射问题长什么样：如果外层键写对了、但记录里的 `seq` 对不上任务包，脚本会在 `build_round` 里**直接抛错**：

```
第 1 轮：作答记录中的 seq 不在任务包内: ['Unit 1/第5课时/III/12']
```

**记住这个区别：抛错 = 题号对不上；静默全零 = 外层键写错了。**

## 二、能自动修的形状

| 上游写成 | 处理 |
| --- | --- |
| `{"items": [...]}`、`{"answers": [...]}`、`{"records": [...]}`、`{"结果": [...]}` | 改名成 `responses` |
| 最外层直接是数组 `[{...}, {...}]` | 包成 `{"responses": [...]}` |
| 单条里用 `answer` / `student_answer` / `ans` / `答案` | 归一成 `response` |
| 单条里用 `hints` / `hint` / `hint_level` / `提示` | 归一成 `hints_used` |
| 单条里用 `no` / `index` / `idx` / `题号` | 归一成 `seq` |
| 单条里只有 `qid` 或 `locator`，没有 `seq` | 回任务包查出 `seq` |
| `locator` 写成字符串 `"Unit 1/第3课时/II/6"` 或对象 `{unit,period,...}` | 两种都能匹配 |
| `locator` 少写一级（只给了 `"6"`） | 按末段匹配 |
| 篇章材料的作答写成数组 `["B","C","D"]` | 按小题号还原成 `{"8":"B",...}` |
| 缺 `hints_used` | 按 0 计，并在 `repairs` 里提醒会让掌握度偏高 |
| 任务包里有、记录里没有的题 | 按未作答处理 |

**学生的原始作答一个字都不改**——不修拼写、不改大小写、不去标点。`report_engine.is_correct` 自己会做归一化（`"With his left hand."` 判得对），替它清洗反而可能把错的洗成对的。

## 三、`issues` 逐条处理

`issues` 非空时脚本退出码 2，**必须停下**。

### 「任务包里 seq [...] 缺 acceptable_answers，判不了分」

最常见的一条。`grade_item` 遇到这个会直接抛错。

- **教辅原题**：回 `assets/questions/` 按 `locator` 取标准答案填上。
- **生成题**：出题时就该自带，漏了说明上游生成环节有 bug，回去补。
- **英语填空注意多解**：`acceptable_answers` 是数组，`loud` / `loudly`、`by bus` / `on a bus` 这类都要列全，漏列会把对的判成错的。

**绝不能凭印象补答案。** 补错了整份报告的数字都是错的，而且从报告表面看不出来。

### 「第 N 条记录对不上任务包里的任何一道题」

记录里的题号、qid、locator 三样都没能匹配上。常见原因：

- 传错了文件（第 1 轮的记录配第 2 轮的任务包）——先核对 `session_id`
- 上游按自己的编号记（`"Q4"`），而任务包里是 `seq: 4`——让上游改用 `seq`，或在记录里补 `locator`
- 题库重新抽取过，locator 变了——这是 `student-ability-tiering` 已知缺口里承认的问题

### 「seq N 在同一份记录里出现了两次」

后一条会覆盖前一条。可能是学生重做了一遍，也可能是上游记重了。**要问清楚哪条是真的**——如果是重做，按「不合并轮次」的规矩，重做应该作为第 2 轮 `append`，不是覆盖第 1 轮。

### 「归一化之后一道题都没有作答内容」

整份记录只有题号没有答案。基本可以确定是传错了文件——比如把任务包当成作答记录传了。

## 四、`repairs` 要如实交代

`repairs` 是已经自动修掉的，但**不能当没发生过**。在给调用方的说明里列出来，尤其这两条：

- **「按出现顺序对到 seq N（请人工确认）」**——这是兜底不是保证。上游记录顺序与任务包不一致时会对错，而且对错了不会有任何报错。
- **「没记提示级数，按 0 计」**——漏记提示会让 `accuracy_independent` 偏高，进而让下一轮推超纲题。多几条就要回去让上游把提示记全。

## 五、多轮怎么办

一轮一份任务包、一份作答记录，各跑一次 `intake.py`，再按先后 `append` 进同一个会话文件：

```bash
python scripts/intake.py --pack r1_pack.json --log r1_raw.json --out r1_log.json
python scripts/intake.py --pack r2_pack.json --log r2_raw.json --out r2_log.json

R=../student-ability-tiering/scripts/report_engine.py
python $R append --session sess.json --pack r1_pack.json
python $R append --session sess.json --log  r1_log.json
python $R append --session sess.json --pack r2_pack.json
python $R append --session sess.json --log  r2_log.json
python $R report --session sess.json --profile profile.json > report.json
```

`append` 的语义是追加，`--pack` 和 `--log` 要交替配对。**顺序错了会把第 2 轮的作答记到第 1 轮头上**，而且不报错。

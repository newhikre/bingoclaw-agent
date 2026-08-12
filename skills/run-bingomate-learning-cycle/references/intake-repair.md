# 作答记录入口修复

`scripts/intake.py` 能修什么、不能修什么，以及 `issues` 逐条怎么处理。

## 一、为什么必须先跑这一步

旧链路曾经只靠 `seq` 配对，而且允许绕过入口直接调用报告脚本：

```python
records = {r.get("seq"): r for r in log.get("responses", [])}
```

这会造成三个连锁后果：

1. 外层键不是 `responses`（写成 `items`、`answers`、或直接是个数组），`log.get("responses", [])` 返回空列表；
2. 每道题拿到 `record=None`，`grade_item` 判成「未作答」；
3. **脚本不报错，正常出报告，所有数字是零。**

这是最危险的一种失败：它长得像一份正常报告。微信通道实测已经踩过——当时看到「全未作答」，误判成 qid 与 locator 的映射问题，反复改任务包无果，最后手工核对凑了一份报告交出去，违反了「以脚本结果为准」。

统一 Skill 已固定先跑 `intake.py`，并用 `item_id` 为主键、`seq` 为兼容展示序号。外层键错误或整份空作答会让 `append-log` 停在 `needs_internal_repair`，不再生成全零报告。真正的映射问题会直接抛错：

```
第 1 轮：作答记录中的 seq 不在任务包内: ['Unit 1/第5课时/III/12']
```

**不论外层键还是题目对应关系有问题，都先修输入；不得手工判分。**

## 二、能自动修的形状

| 上游写成 | 处理 |
| --- | --- |
| `{"items": [...]}`、`{"answers": [...]}`、`{"records": [...]}`、`{"结果": [...]}` | 改名成 `responses` |
| 最外层直接是数组 `[{...}, {...}]` | 包成 `{"responses": [...]}` |
| 单条里用 `answer` / `student_answer` / `ans` / `答案` | 归一成 `response` |
| 单条里用 `hints` / `hint` / `hint_level` / `提示` | 归一成 `hints_used` |
| 单条里用 `no` / `index` / `idx` / `题号` | 归一成 `seq` |
| 单条里有 `item_id` | 优先按会话内唯一主键回溯，并与 `seq` 交叉校验 |
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

- **教辅原题**：回 `../assets/questions/` 按 `locator` 取标准答案填上。
- **生成题**：出题时就该自带，漏了说明上游生成环节有 bug，回去补。
- **英语填空注意多解**：`acceptable_answers` 是数组，`loud` / `loudly`、`by bus` / `on a bus` 这类都要列全，漏列会把对的判成错的。

**绝不能凭印象补答案。** 补错了整份报告的数字都是错的，而且从报告表面看不出来。

### 「第 N 条记录对不上任务包里的任何一道题」

记录里的 `item_id`、展示序号、来源题号和定位都没能匹配上。常见原因：

- 传错了文件（第 1 轮的记录配第 2 轮的任务包）——先核对 `session_id`
- 上游按自己的编号记（`"Q4"`），而任务包里有正式 `item_id`——让上游保存任务下发时的 `item_id`
- 题库重新抽取过，`locator` 变了——回到本轮已保存任务包，不要拿新题库重新推断

### 「seq N 在同一份记录里出现了两次」

重复记录会被拒绝。可能是学生重做了一遍，也可能是上游记重了。**要问清楚哪条是真的**——如果是重做，应作为下一轮任务，而不是覆盖本轮。

### 「归一化之后一道题都没有作答内容」

整份记录只有题号没有答案。基本可以确定是传错了文件——比如把任务包当成作答记录传了。

## 四、`repairs` 要如实交代

`repairs` 是已经自动修掉的，但**不能当没发生过**。写进内部留档，不能原样发给学生或家长，尤其关注这两条：

- **「按出现顺序对到 seq N（请人工确认）」**——这是兜底不是保证。上游记录顺序与任务包不一致时会对错，而且对错了不会有任何报错。
- **「没记提示级数，按 0 计」**——漏记提示会让 `accuracy_independent` 偏高，进而让下一轮推超纲题。多几条就要回去让上游把提示记全。

## 五、多轮怎么办

一轮一份任务包、一份作答记录，按先后追加进同一个统一状态：

```bash
python scripts/cycle_engine.py append-pack --state state.json --pack r1_pack.json
python scripts/cycle_engine.py append-log --state state.json --log r1_raw.json
python scripts/cycle_engine.py append-pack --state state.json --pack r2_pack.json --continue-before-report
python scripts/cycle_engine.py append-log --state state.json --log r2_raw.json
python scripts/cycle_engine.py report --state state.json --out-dir report-out
```

通常第一轮后直接报告；只有同次学习确实继续时才使用 `--continue-before-report`。`append-pack` 和 `append-log` 必须交替，强制 `item_id` 与阶段校验会阻止跨轮静默覆盖。

# 端到端样例

一份跑得通的完整数据，可直接用来验证环境或做演示彩排。

## 这份样例演的是什么

C 层学生小宇，两轮：

- **第 1 轮** 4 道题。`by + 动名词` 连错两道（一道要提示才对、一道做错），任务型阅读三小题对两个。
- **讲解**（不在数据里，是对话里发生的）
- **第 2 轮** 2 道题，同一个考点 `by + 动名词`，两道全对、零提示。

这正是四段式链路最该被看见的东西：**一开始不会 → 讲完自己做对了**。报告会把它讲成一条恢复叙事，而不是把两轮揉成一个平均分。

`r1_raw.json` 故意写成了四种坏格式，全部来自微信通道实测里真实出现过的写法：

- 最外层直接是数组，不是 `{"responses": [...]}`
- 前两题只有 `qid` 字符串，没有 `seq`
- 用 `answer` / `hint` 而不是 `response` / `hints_used`
- 第 4 题用了中文键 `题号` / `答案` / `提示`，且作答写成数组

`r2_raw.json` 用的是 `{"items": [...]}` —— **就是这个键害得实测那次判出「全未作答」**。

## 怎么跑

```bash
cd assets/example
I=../../scripts/intake.py
C=../../scripts/compose.py
R=../../../student-ability-tiering/scripts/report_engine.py

python $I --pack r1_pack.json --log r1_raw.json --out r1_log.json
python $I --pack r2_pack.json --log r2_raw.json --out r2_log.json

python $R append --session sess.json --pack r1_pack.json
python $R append --session sess.json --log  r1_log.json
python $R append --session sess.json --pack r2_pack.json
python $R append --session sess.json --log  r2_log.json
python $R report --session sess.json --profile profile.json > report.json

python $C --report report.json --profile profile.json --name 小宇 --out-dir out/
```

`student.txt` / `parent.txt` / `teacher.txt` 是预期产出，拿你跑出来的对一下即可。

## 预期的关键数字

- 6 道题、8 小题，没用提示自己做对 5 小题（62.5%），算上提示共 75.0%
- 第 1 轮自己做对 50.0%，第 2 轮 100.0%
- `by + 动名词` 标记为已恢复，不出现在「还欠着」里
- 词形变化、语法辨析、汉英转换三项本次考得太少，标「不足以判断」

## 故意留着的坑

第 1 轮没有记 `minutes_actual`（原始记录里就没有）。所以家长版**不报总用时**——只有一轮有数就不能加起来当总数。这是对的行为，不是 bug。

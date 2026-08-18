# 统一状态契约

`cycle_engine.py` 维护一份调用方指定位置的 `state.json`。运行态文件不得写进 Skill 目录；OpenClaw 类平台应把它放进该学生的会话存储或业务数据目录。

## 一、状态结构

```json
{
  "schema_version": "1.0",
  "session_id": "bm-20260812-143000-a1b2c3",
  "created_at": "2026-08-12T14:30:00+08:00",
  "updated_at": "2026-08-12T14:40:00+08:00",
  "learner": {
    "name": "小宇",
    "grade": "九年级",
    "learned_units": ["Unit 1", "Unit 2"],
    "available_minutes": 20,
    "personality": "喜欢直接说明重点",
    "feedback_preference": "先给思路"
  },
  "profile": {},
  "rounds": [],
  "reports": [],
  "latest_report": null,
  "profile_observations": [],
  "strategy_history": [],
  "identity_confirmation": {
    "required": false,
    "confirmed": true,
    "learner_name": "小宇",
    "requested_at": "2026-08-12T14:41:00+08:00",
    "confirmed_at": "2026-08-12T14:41:05+08:00"
  }
}
```

- `learner` 保存建档时答到的基本信息，包括称呼、年级、已学单元、可用时长和交互偏好；不得推断或编造。年级只存档，不参与判层。
- `diagnose` 会用 `learner.learned_units`、`available_minutes`、`feedback_preference` 补齐诊断会话的缺省值；诊断文件显式值优先。
- `profile` 保存 `profile_engine.evaluate` 的完整结果，不只保存 A/B/C。
- `rounds` 只追加，不覆盖旧轮次。每轮同时保存任务包、原始作答、错题订正状态、归一化作答、入口修复信息和报告归属。
- `reports` 保存报告元数据；完整报告写到命令指定的输出目录。
- `profile_observations` 保存每次回写的原始补丁与 `observed_at`，用于追溯，不冒充跨会话趋势。
- `strategy_history` 只在显式 `rediagnose` 成功时保存被替换的旧画像和策略。
- `identity_confirmation` 是本次 Skill 触发的身份门禁。它不代替画像，也不把确认结果写进学习成绩；旧版状态缺少该字段时由脚本自动补齐。

## 二、阶段计算

每次 Skill 新触发先由 `activate` 检查指定状态；同一次连续学习过程再由 `status` 计算阶段：

1. 找到有效画像但本次尚未确认身份：`needs_identity_confirmation`
2. 无有效画像：`needs_diagnostic`
3. 有错题等待提示后重答、讲解或确认：`needs_remediation`
4. 有未归一化作答且入口报告问题：`needs_internal_repair`
5. 有任务包但尚无作答：`awaiting_responses`
6. 有尚未报告的真实作答：`ready_for_report`。这个值表示“已具备报告条件、正在等待学生选择”，不是自动出报告；固定等待结束、继续或讲解三选一
7. 其余有画像状态：`ready_for_task`

`activate` 的身份分支是确定性的：

- 状态不存在或没有有效画像：不询问身份，进入 `needs_diagnostic`。
- 已有有效画像：写入待确认门禁，只返回一句 `identity_confirmation.student_prompt`。在 `confirm-identity` 成功前，`append-pack` 等后续动作会被阶段门禁拒绝。
- 回答 `yes`：继续使用原状态、原画像和未完成轮次；没有未完成轮次时直接进入 `ready_for_task`，跳过画像建模。
- 回答 `no`：在原状态旁创建一份带新 `session_id` 的状态，返回新 `state` 路径并进入 `needs_diagnostic`；旧状态不覆盖、不删除。

新诊断若所有作答均为空或不可评分，仍可产出内部临时 B 策略，但 `item_count=0`，状态继续停在 `needs_diagnostic`，不得据此推学习任务。

## 三、统一题目标识

每个任务项必须有会话内唯一的 `item_id`。若任务包没有提供，`append-pack` 自动生成：

```text
<session_id>-r<round>-i<seq>
```

- 作答关联以 `item_id` 为主键。
- `seq` 仅用于学生看到的顺序和兼容旧日志。
- `source_qid`、`qid` 和 `locator` 只用于题库溯源与防重复，不再承担跨阶段主键职责。
- `append-pack` 拒绝重复的 `item_id`、`seq`、来源题号和教辅原题定位。
- `item_id` 在整个状态的全部历史轮次中唯一，不只是单轮唯一。
- `intake.py` 兼容旧日志的 `seq`、`qid`、`locator`，但输出统一补回 `item_id + seq`。

`append-pack` 还会执行范围门禁：本轮与每题单元必须属于画像 `scope.units`；`part`、`ability`、`source` 必须是已知枚举；变式题和生成题必须有 `self_check_passed: true` 与 `in_scope: true`。已进入排除清单的教辅原题默认拒绝，只有明确订正时才可同时设置 `repeat_for_correction: true` 与非空 `repeat_reason`。

## 四、答案字段

闭环内部只使用：

- 学生作答：顶层 `responses`，单题字段 `response`
- 标准答案：任务项字段 `acceptable_answers`

`answers`、`items`、`answer` 等别名只允许出现在 `intake.py` 的入口兼容层。它们归一化后不得继续流入下游。任务包中的 `acceptable_answers` 必须是数组，篇章题可用 `{小题号: 答案数组}` 对象。

## 五、画像回写规则

`report` 固定执行 `normalize → validate → grade → render → apply patch`，报告成功后自动合并 `profile_patch`：

- `null` 不覆盖已有观测。
- `exclude_qids` / `cumulative_exclude_item_ids` 与旧值做保序并集。
- `mastery_observed` 更新两个难度层的最新观测。
- `by_ability_observed` 更新对应能力维度，不删除本次未测维度。
- `misconceptions_observed` 做并集；`error_pattern` 仅在非空时更新。
- `focus_abilities_hint` 非空时作为下一轮重点。
- `strategy.code`、`strategy.name` 和判层理由绝不由报告改写；改变 A/B/C 必须重新摸底。
- 每次补丁连同 `observed_at` 写入 `profile_observations`。

## 六、命令

```bash
python scripts/cycle_engine.py activate --state state.json
python scripts/cycle_engine.py confirm-identity --state state.json --answer yes
python scripts/cycle_engine.py confirm-identity --state state.json --answer no
python scripts/cycle_engine.py init --state state.json [--learner learner.json]
python scripts/cycle_engine.py status --state state.json
python scripts/cycle_engine.py diagnose --state state.json --session diagnostic-session.json
python scripts/cycle_engine.py rediagnose --state state.json --session diagnostic-session-2.json
python scripts/cycle_engine.py adopt-profile --state state.json --profile profile.json
python scripts/cycle_engine.py append-pack --state state.json --pack task-pack.json
python scripts/cycle_engine.py append-pack --state state.json --pack round2-pack.json --continue-before-report
python scripts/cycle_engine.py append-log --state state.json --log raw-log.json
python scripts/cycle_engine.py append-log --state state.json --log raw-log.json --student-skipped-remediation
python scripts/cycle_engine.py report --state state.json --out-dir report-out --user-ended
python scripts/cycle_engine.py apply-patch --state state.json --patch report-or-patch.json
python scripts/cycle_engine.py validate
```

命令错误统一返回：

```json
{"ok": false, "visibility": "internal", "error": "..."}
```

这类内容只供智能体修复，不能原样发给学生或家长。

`activate` 是每次新触发的唯一入口，不是每条学生消息都调用。返回 `needs_identity_confirmation` 时，只发送 `identity_confirmation.student_prompt`；不要展示字段、阶段或命令。`confirm-identity --answer no` 返回的新 `state` 路径从此作为当前学生状态，旧路径留给原学生。

`status` 和 `append-log` 在 `needs_remediation` 时返回 `practice_guidance`。模型只发送其中的学生话术并等待真实重答，不展示收尾选择；只有学生明确拒绝订正时才可使用 `--student-skipped-remediation`。

`status` 和成功的 `append-log` 在 `ready_for_report` 时返回 `round_completion_choice`。模型必须先发送本组答题反馈，再显示三项自然语言选择。`report` 缺少 `--user-ended` 时会拒绝执行；该标记只可在学生明确选择结束或主动要求学习总结后使用。选择继续时用 `--continue-before-report`；选择讲解不改变阶段，讲完重新显示三项选择。

---
name: assess-learning-strategy
description: Build a lightweight student profile from basic learning context and a short diagnostic drawn from the project's Unit 1/Unit 2 exercise JSON, then deterministically recommend an A, B, or C learning strategy with evidence and confidence. Use when a learner starts the BingoClaw teaching-aid demo, asks for an initial assessment, needs user-profile cold start, or needs an ABC strategy decision before personalized learning. Stop after strategy selection; do not conduct the personalized lesson or generate a learning report unless the user explicitly expands the scope.
---

# Assess Learning Strategy

Create a cold-start learner profile and select one scoped learning strategy:

- `A · 拓展挑战`
- `B · 巩固提升`
- `C · 稳固基础`

Treat the result as a strategy for the assessed subject and units, not a permanent label for the learner.

## Workflow

### 1. Establish the assessment scope

Read the metadata in the available exercise JSON before asking questions. This demo currently supports:

- `../../resource/1.2单元及json/unit1_questions.json`
- `../../resource/1.2单元及json/unit2_questions.json`

Only assess units the learner says they have studied. Never count an unstudied unit as weak.

### 2. Collect the minimum profile

Ask for missing fields in one concise turn:

- grade
- textbook version
- studied/current units
- learning goal
- available minutes per session
- recent score range, if known

Keep name and school optional. Treat self-reported scores and weak areas as context, not diagnostic truth.

### 3. Run the short diagnostic

Generate the default eight diagnostic questions, balanced across Unit 1 and Unit 2:

```powershell
python scripts/strategy_engine.py questions --units "Unit 1" "Unit 2" --count-per-unit 4
```

Adjust `--units` to include only studied units. Present questions one at a time or in a compact numbered batch. Do not reveal answers, strategy thresholds, or correctness before all answers are collected. Do not provide hints during the diagnostic; if the learner asks for help, record that the item is not independently completed.

The diagnostic bank only adds answer, difficulty, and knowledge-point metadata. The displayed stems and options are always loaded from the existing project JSON.

### 4. Evaluate deterministically

Create a temporary session JSON matching the schema in [strategy-model.md](references/strategy-model.md), then run:

```powershell
python scripts/strategy_engine.py evaluate --input <session-json-path>
```

Use the script result as the authoritative A/B/C decision. Do not manually change thresholds or force a population ratio. If fewer than four valid responses exist, return a provisional strategy with low confidence and say that more evidence is needed.

### 5. Return the decision and stop

Respond in Chinese with exactly these logical sections:

1. `画像摘要` — grade, version, assessed units, goal, available time.
2. `策略判定` — code and name, for example `B · 巩固提升`.
3. `判定依据` — overall, basic, and application/challenge performance plus the strongest/weakest dimensions.
4. `策略参数` — difficulty mix, pacing, and feedback style returned by the script.
5. `置信度与范围` — confidence and an explicit statement that the result applies only to the assessed units.

Do not start the personalized exercise session and do not generate a learning report in this version.

## Commands

Run the following commands from this skill directory.

Validate that every diagnostic locator still resolves against the source JSON:

```powershell
python scripts/strategy_engine.py validate-bank
```

Use [strategy-model.md](references/strategy-model.md) when explaining fields, reviewing thresholds, or integrating the output into another agent.

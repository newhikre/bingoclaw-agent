#!/usr/bin/env python3
"""Select diagnostic questions and deterministically recommend an ABC strategy."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR.parent.parent
BANK_PATH = SKILL_DIR / "assets" / "diagnostic_bank.json"
DIFFICULTY_WEIGHTS = {1: 1.0, 2: 1.25, 3: 1.5}

STRATEGIES = {
    "A": {
        "name": "拓展挑战",
        "policy": {
            "difficulty_mix": {"基础": 20, "应用": 40, "挑战": 40},
            "pace": "快节奏，允许跳过已掌握基础内容",
            "feedback_style": "启发式，以追问、迁移和变式为主",
        },
    },
    "B": {
        "name": "巩固提升",
        "policy": {
            "difficulty_mix": {"基础": 40, "应用": 40, "挑战": 20},
            "pace": "中等节奏，围绕薄弱点集中练习",
            "feedback_style": "先提示思路，再按需要分步展开",
        },
    },
    "C": {
        "name": "稳固基础",
        "policy": {
            "difficulty_mix": {"基础": 70, "应用": 25, "挑战": 5},
            "pace": "慢节奏，小步递进并增加确认",
            "feedback_style": "概念和例题优先，使用陪伴式反馈",
        },
    },
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def load_bank() -> list[dict[str, Any]]:
    bank = load_json(BANK_PATH)
    return bank["items"]


def resolve_question(
    item: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_path = PROJECT_ROOT / item["source_file"]
    data = load_json(source_path)
    locator = item["locator"]

    matching_sections = [
        section
        for section in data["sections"]
        if section.get("period") == locator["period"]
    ]
    for section in matching_sections:
        for exercise in section.get("exercises", []):
            if exercise.get("exercise_no") != locator["exercise_no"]:
                continue
            for question in exercise.get("questions", []):
                if question.get("no") == locator["question_no"]:
                    return data, exercise, question

    raise ValueError(
        f"Cannot resolve {item['id']} at {item['source_file']} "
        f"{locator['period']}/{locator['exercise_no']}/Q{locator['question_no']}"
    )


def public_question(item: dict[str, Any]) -> dict[str, Any]:
    data, exercise, question = resolve_question(item)
    result: dict[str, Any] = {
        "id": item["id"],
        "unit": item["unit"],
        "unit_title": data["metadata"].get("unit_title"),
        "type": exercise.get("type"),
        "difficulty": item["difficulty"],
        "dimension": item["dimension"],
        "knowledge_point": item["knowledge_point"],
        "stem": question.get("stem"),
    }
    if question.get("chinese"):
        result["chinese"] = question["chinese"]
    if question.get("given_word"):
        result["given_word"] = question["given_word"]
    if question.get("options"):
        result["options"] = question["options"]
    return {key: value for key, value in result.items() if value is not None}


def normalize_answer(value: Any) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[，,；;：:。.!！?？'\"`()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def answer_is_correct(given: Any, accepted: list[str]) -> bool:
    normalized = normalize_answer(given)
    accepted_normalized = {normalize_answer(value) for value in accepted}
    if not normalized:
        return False
    if normalized in accepted_normalized:
        return True
    option_match = re.fullmatch(r"(?:答案|选)?\s*([a-d])(?:\s+.*)?", normalized)
    return bool(option_match and option_match.group(1) in accepted_normalized)


def percent(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 1)


def unweighted_group_scores(
    results: list[dict[str, Any]], field: str
) -> dict[str, float]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for result in results:
        grouped[str(result[field])].append(result["correct"])
    return {
        key: round(sum(values) / len(values) * 100, 1)
        for key, values in sorted(grouped.items())
    }


def choose_strategy(
    overall: float | None,
    basic: float | None,
    advanced: float | None,
    advanced_count: int,
    recent_score: Any,
) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    limitations: list[str] = []

    if overall is None:
        limitations.append("没有有效诊断答案，策略仅为低置信度临时建议。")
        try:
            recent = float(recent_score)
        except (TypeError, ValueError):
            reasons.append("缺少诊断结果和近期成绩，采用中性的巩固提升策略。")
            return "B", reasons, limitations
        if recent >= 85:
            reasons.append(f"近期成绩为 {recent:g}，达到临时 A 策略阈值。")
            return "A", reasons, limitations
        if recent >= 60:
            reasons.append(f"近期成绩为 {recent:g}，处于临时 B 策略区间。")
            return "B", reasons, limitations
        reasons.append(f"近期成绩为 {recent:g}，低于临时 C 策略阈值。")
        return "C", reasons, limitations

    if (
        overall >= 80
        and basic is not None
        and basic >= 80
        and advanced_count > 0
        and advanced is not None
        and advanced >= 60
    ):
        reasons.append(
            f"综合分 {overall:g}，基础分 {basic:g}，应用/挑战分 {advanced:g}。"
        )
        reasons.append("基础掌握稳定，并能处理应用或挑战题。")
        return "A", reasons, limitations

    if overall < 50 or (basic is not None and basic < 60):
        reasons.append(
            f"综合分 {overall:g}，基础分 {basic if basic is not None else '无'}。"
        )
        reasons.append("当前范围存在核心基础缺口，应优先稳固基础。")
        return "C", reasons, limitations

    reasons.append(
        f"综合分 {overall:g}，基础分 {basic if basic is not None else '无'}，"
        f"应用/挑战分 {advanced if advanced is not None else '无'}。"
    )
    reasons.append("基础已有一定积累，但仍需要围绕薄弱点巩固提升。")
    return "B", reasons, limitations


def confidence_for(results: list[dict[str, Any]], fallback_only: bool) -> str:
    if fallback_only:
        return "低"
    units = {result["unit"] for result in results}
    has_basic = any(result["difficulty"] == 1 for result in results)
    has_advanced = any(result["difficulty"] >= 2 for result in results)
    if len(results) >= 8 and len(units) >= 2 and has_basic and has_advanced:
        return "高"
    if len(results) >= 4 and has_basic and has_advanced:
        return "中"
    return "低"


def command_questions(args: argparse.Namespace) -> None:
    if args.count_per_unit < 1:
        raise ValueError("count-per-unit must be at least 1")
    bank = load_bank()
    available_units = sorted({item["unit"] for item in bank})
    requested_units = args.units or available_units
    unknown = sorted(set(requested_units) - set(available_units))
    if unknown:
        raise ValueError(f"Unsupported units: {', '.join(unknown)}")

    selected: list[dict[str, Any]] = []
    for unit in requested_units:
        candidates = sorted(
            (item for item in bank if item["unit"] == unit),
            key=lambda item: (item["priority"], item["id"]),
        )
        selected.extend(candidates[: args.count_per_unit])

    emit(
        {
            "scope": {"units": requested_units, "question_count": len(selected)},
            "selected_question_ids": [item["id"] for item in selected],
            "questions": [public_question(item) for item in selected],
        }
    )


def command_evaluate(args: argparse.Namespace) -> None:
    session = json.load(sys.stdin) if args.input == "-" else load_json(Path(args.input))
    profile = session.get("profile", {})
    bank_by_id = {item["id"]: item for item in load_bank()}
    answers = session.get("answers", {})
    if isinstance(answers, list):
        answers = {entry["id"]: entry.get("answer") for entry in answers}
    if not isinstance(answers, dict):
        raise ValueError("answers must be an object or a list of {id, answer}")

    expected_ids = session.get("selected_question_ids") or list(answers)
    unknown_ids = sorted(set(expected_ids) - set(bank_by_id))
    if unknown_ids:
        raise ValueError(f"Unknown diagnostic ids: {', '.join(unknown_ids)}")
    studied_units = set(profile.get("studied_units", []))
    assessed_units_requested = {bank_by_id[question_id]["unit"] for question_id in expected_ids}
    if studied_units and not assessed_units_requested.issubset(studied_units):
        unstudied = sorted(assessed_units_requested - studied_units)
        raise ValueError(
            "Diagnostic contains units not listed as studied: " + ", ".join(unstudied)
        )

    results: list[dict[str, Any]] = []
    for question_id in expected_ids:
        if question_id not in answers or normalize_answer(answers[question_id]) == "":
            continue
        item = bank_by_id[question_id]
        results.append(
            {
                "id": question_id,
                "unit": item["unit"],
                "dimension": item["dimension"],
                "knowledge_point": item["knowledge_point"],
                "difficulty": item["difficulty"],
                "correct": answer_is_correct(
                    answers[question_id], item["answers"]
                ),
            }
        )

    earned = 0.0
    possible = 0.0
    basic_correct = basic_count = 0
    advanced_correct = advanced_count = 0
    for result in results:
        weight = DIFFICULTY_WEIGHTS[result["difficulty"]]
        possible += weight
        if result["correct"]:
            earned += weight
        if result["difficulty"] == 1:
            basic_count += 1
            basic_correct += int(result["correct"])
        else:
            advanced_count += 1
            advanced_correct += int(result["correct"])

    overall = percent(earned, possible)
    basic = percent(basic_correct, basic_count)
    advanced = percent(advanced_correct, advanced_count)
    strategy_code, reasons, limitations = choose_strategy(
        overall, basic, advanced, advanced_count, profile.get("recent_score")
    )

    missing_count = max(0, len(expected_ids) - len(results))
    if missing_count:
        limitations.append(f"有 {missing_count} 道诊断题未形成有效答案。")
    if len(results) < 4:
        limitations.append("有效诊断题少于 4 道，建议补充作答后再确认策略。")

    fallback_only = not results
    confidence = confidence_for(results, fallback_only)
    policy = STRATEGIES[strategy_code]
    assessed_units = sorted({result["unit"] for result in results})
    if not assessed_units:
        assessed_units = profile.get("studied_units", [])

    output: dict[str, Any] = {
        "profile": {
            "grade": profile.get("grade"),
            "textbook_version": profile.get("textbook_version"),
            "studied_units": profile.get("studied_units", []),
            "goal": profile.get("goal"),
            "available_minutes": profile.get("available_minutes"),
            "recent_score": profile.get("recent_score"),
        },
        "scope": {
            "subject": "英语",
            "assessed_units": assessed_units,
            "answered_questions": len(results),
            "expected_questions": len(expected_ids),
        },
        "scores": {
            "overall_weighted": overall,
            "basic": basic,
            "application_and_challenge": advanced,
            "by_unit": unweighted_group_scores(results, "unit") if results else {},
            "by_dimension": (
                unweighted_group_scores(results, "dimension") if results else {}
            ),
        },
        "strategy": {
            "code": strategy_code,
            "name": policy["name"],
            "confidence": confidence,
            "reasons": reasons,
            "policy": policy["policy"],
        },
        "limitations": limitations,
    }
    if args.include_item_results:
        output["item_results"] = results
    emit(output)


def command_validate_bank(_: argparse.Namespace) -> None:
    validated: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in load_bank():
        try:
            data, exercise, question = resolve_question(item)
            if not item.get("answers"):
                raise ValueError("missing accepted answers")
            options = question.get("options", {})
            letter_answers = [
                answer
                for answer in item["answers"]
                if re.fullmatch(r"[A-Da-d]", answer)
            ]
            for answer in letter_answers:
                if answer.upper() not in options:
                    raise ValueError(f"answer option {answer.upper()} is absent")
            validated.append(
                {
                    "id": item["id"],
                    "unit": data["metadata"].get("unit"),
                    "type": exercise.get("type"),
                    "question_no": question.get("no"),
                }
            )
        except Exception as exc:  # Report every broken locator in one run.
            errors.append(f"{item.get('id', '<unknown>')}: {exc}")

    payload = {
        "valid": not errors,
        "validated_count": len(validated),
        "error_count": len(errors),
        "errors": errors,
        "items": validated,
    }
    emit(payload)
    if errors:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    questions_parser = subparsers.add_parser(
        "questions", help="Print diagnostic questions"
    )
    questions_parser.add_argument(
        "--units", nargs="+", help="Studied units, e.g. Unit 1 Unit 2"
    )
    questions_parser.add_argument("--count-per-unit", type=int, default=4)
    questions_parser.set_defaults(handler=command_questions)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="Evaluate a diagnostic session"
    )
    evaluate_parser.add_argument(
        "--input", required=True, help="Path to session JSON, or - to read stdin"
    )
    evaluate_parser.add_argument("--include-item-results", action="store_true")
    evaluate_parser.set_defaults(handler=command_evaluate)

    validate_parser = subparsers.add_parser(
        "validate-bank", help="Validate bank locators"
    )
    validate_parser.set_defaults(handler=command_validate_bank)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()

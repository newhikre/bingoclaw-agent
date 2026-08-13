#!/usr/bin/env python3
"""BingoMate learning-cycle orchestrator.

This is the only recommended entry point for the aggregate skill.  It keeps
learner metadata, the diagnostic profile, task rounds, normalized responses,
reports, and profile observations in one caller-owned state file.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import compose
import intake
import profile_engine
import report_engine


SCHEMA_VERSION = "1.0"
TIER_CODES = {"A", "B", "C"}
PARTS = set(report_engine.PARTS)
ABILITIES = set(report_engine.ABILITIES)
SOURCES = {"workbook", "variant", "generated"}
DEMO_UNITS = {"Unit 1", "Unit 2"}


def configure_utf8_stdio() -> None:
    """Keep Chinese CLI output readable on Windows without requiring -X utf8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


configure_utf8_stdio()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_object(path: Path, label: str) -> dict:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return value


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    tmp = path.with_name(f".{path.name}.{token}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def new_session_id() -> str:
    return f"bm-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def new_state(learner: dict | None = None) -> dict:
    created = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": new_session_id(),
        "created_at": created,
        "updated_at": created,
        "learner": copy.deepcopy(learner or {}),
        "profile": None,
        "rounds": [],
        "reports": [],
        "latest_report": None,
        "profile_observations": [],
        "strategy_history": [],
    }


def validate_state_shape(state: dict) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"state.schema_version 必须是 {SCHEMA_VERSION}，实际为 {state.get('schema_version')!r}"
        )
    if not isinstance(state.get("learner"), dict):
        raise ValueError("state.learner 必须是对象")
    for key in ("rounds", "reports", "profile_observations", "strategy_history"):
        if not isinstance(state.get(key), list):
            raise ValueError(f"state.{key} 必须是数组")
    numbers = [round_data.get("round") for round_data in state["rounds"]]
    if any(not isinstance(number, int) or number < 1 for number in numbers):
        raise ValueError("state.rounds[].round 必须是正整数")
    if len(numbers) != len(set(numbers)):
        raise ValueError("state.rounds[].round 存在重复")


def load_state(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"状态文件不存在：{path}；先运行 init")
    state = load_object(path, "状态文件")
    state.setdefault("strategy_history", [])
    validate_state_shape(state)
    return state


def save_state(path: Path, state: dict) -> None:
    state["updated_at"] = now_iso()
    validate_state_shape(state)
    atomic_write_json(path, state)


def profile_is_ready(profile: Any) -> bool:
    if not isinstance(profile, dict):
        return False
    strategy = profile.get("strategy") or {}
    if strategy.get("code") not in TIER_CODES:
        return False
    units = (profile.get("scope") or {}).get("units")
    if not isinstance(units, list) or not units or not {str(unit) for unit in units}.issubset(DEMO_UNITS):
        return False
    evidence = ((profile.get("characteristics") or {}).get("evidence") or {})
    # Legacy profiles may not have evidence.  A freshly computed profile with
    # zero gradable responses stays in diagnostic rather than scheduling work.
    return evidence.get("item_count") != 0


def phase_of(state: dict) -> str:
    if not profile_is_ready(state.get("profile")):
        return "needs_diagnostic"

    for round_data in reversed(state["rounds"]):
        if round_data.get("task_pack") and not round_data.get("normalized_log"):
            intake_state = round_data.get("intake") or {}
            if round_data.get("raw_log") is not None or intake_state.get("issues"):
                return "needs_internal_repair"
            return "awaiting_responses"

    if any(
        round_data.get("normalized_log") and not round_data.get("reported_at")
        for round_data in state["rounds"]
    ):
        return "ready_for_report"
    return "ready_for_task"


def round_completion_choice_payload() -> dict:
    """Internal routing hint: the caller renders this as natural student copy."""
    return {
        "required": True,
        "student_prompt": (
            "这组练习完成了。接下来你想：\n"
            "1. 结束本次学习，看看今天的学习总结\n"
            "2. 继续学习，再练一组\n"
            "3. 还有没弄明白的题，先讲一讲（告诉我题号）\n"
            "回复 1、2 或 3 就可以。"
        ),
        "choices": [
            {"code": "1", "intent": "end_learning", "next_action": "report --user-ended"},
            {"code": "2", "intent": "continue_learning", "next_action": "append-pack --continue-before-report"},
            {"code": "3", "intent": "explain_questions", "next_action": "explain then show these choices again"},
        ],
    }


def status_payload(state: dict) -> dict:
    completed = [r for r in state["rounds"] if r.get("normalized_log")]
    pending = [r for r in state["rounds"] if r.get("task_pack") and not r.get("normalized_log")]
    phase = phase_of(state)
    actions = {
        "needs_diagnostic": ["diagnose"],
        "ready_for_task": ["append-pack", "rediagnose"],
        "awaiting_responses": ["append-log"],
        "needs_internal_repair": ["append-log"],
        "ready_for_report": [
            "report --user-ended",
            "append-pack --continue-before-report",
            "explain then show choices again",
        ],
    }[phase]
    payload = {
        "ok": True,
        "schema_version": state["schema_version"],
        "session_id": state["session_id"],
        "phase": phase,
        "allowed_actions": actions,
        "has_profile": profile_is_ready(state.get("profile")),
        "rounds_total": len(state["rounds"]),
        "rounds_completed": len(completed),
        "rounds_pending": len(pending),
        "reports_count": len(state["reports"]),
    }
    if phase == "ready_for_report":
        payload["round_completion_choice"] = round_completion_choice_payload()
    return payload


def merge_non_null(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if value is not None:
            target[key] = copy.deepcopy(value)


def ordered_union(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if item is None:
                continue
            text = str(item)
            if text not in result:
                result.append(text)
    return result


def apply_profile_patch(profile: dict, patch: dict) -> dict:
    """Deterministic profile merge.  It never changes strategy.code/name."""
    updated = copy.deepcopy(profile)
    original_strategy = copy.deepcopy(updated.get("strategy"))

    mastery = updated.setdefault("mastery", {})
    merge_non_null(mastery, patch.get("mastery_observed") or {})
    by_ability = mastery.setdefault("by_ability", {})
    merge_non_null(by_ability, patch.get("by_ability_observed") or {})

    characteristics = updated.setdefault("characteristics", {})
    observed_misconceptions = patch.get("misconceptions_observed") or []
    if observed_misconceptions:
        characteristics["misconceptions"] = ordered_union(
            characteristics.get("misconceptions"), observed_misconceptions
        )
    if patch.get("error_pattern") is not None:
        characteristics["error_pattern"] = patch["error_pattern"]

    rated = {
        key: value
        for key, value in by_ability.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    if rated:
        characteristics["strengths"] = sorted(
            (key for key, value in rated.items() if value >= 80),
            key=lambda key: -rated[key],
        )
        characteristics["weaknesses"] = sorted(
            (key for key, value in rated.items() if value < 60),
            key=lambda key: rated[key],
        )

    next_session = updated.setdefault("next_session", {})
    cumulative = (
        patch.get("cumulative_exclude_item_ids")
        or patch.get("exclude_qids")
        or []
    )
    next_session["exclude_qids"] = ordered_union(
        next_session.get("exclude_qids"), cumulative
    )
    focus = patch.get("focus_abilities_hint")
    if isinstance(focus, list) and focus:
        next_session["focus_abilities"] = copy.deepcopy(focus)

    # Strategy may only change after a new diagnostic run.
    if original_strategy is not None:
        updated["strategy"] = original_strategy
    return updated


def locator_key(value: Any) -> str | None:
    if isinstance(value, str):
        key = "/".join(part.strip() for part in value.split("/") if part.strip())
        return key or None
    if isinstance(value, dict):
        parts = [
            value.get("unit"),
            value.get("period"),
            value.get("exercise_no"),
            value.get("question_no"),
        ]
        key = "/".join(str(part).strip() for part in parts if part not in (None, ""))
        return key or None
    return None


def normalize_pack(pack: dict, state: dict, round_no: int) -> dict:
    normalized = copy.deepcopy(pack)
    state_session = state["session_id"]
    if normalized.get("session_id") not in (None, state_session):
        raise ValueError("任务包 session_id 与当前学习状态不一致")
    normalized["session_id"] = state_session

    profile = state["profile"]
    expected_tier = (profile.get("strategy") or {}).get("code")
    tier = normalized.setdefault("tier", {})
    if not isinstance(tier, dict):
        raise ValueError("任务包 tier 必须是对象")
    if tier.get("level") not in (None, expected_tier):
        raise ValueError("任务包层级与画像 strategy.code 不一致；不得在任务阶段重新判层")
    tier["level"] = expected_tier
    tier.setdefault("name", (profile.get("strategy") or {}).get("name"))
    tier.setdefault("source", "profile.strategy")

    allowed_units_raw = (profile.get("scope") or {}).get("units")
    if not isinstance(allowed_units_raw, list) or not allowed_units_raw:
        raise ValueError("画像 scope.units 缺失，不能校验任务范围")
    allowed_units = {str(unit) for unit in allowed_units_raw}
    session = normalized.get("session")
    if not isinstance(session, dict):
        raise ValueError("任务包 session 必须是对象")
    session_units_raw = session.get("units")
    if not isinstance(session_units_raw, list) or not session_units_raw:
        raise ValueError("任务包 session.units 必须是非空数组")
    session_units = {str(unit) for unit in session_units_raw}
    if not session_units.issubset(allowed_units):
        raise ValueError("任务包 session.units 超出画像 scope.units")

    items = normalized.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("任务包 items 必须是非空数组")

    seqs: set[int] = set()
    item_ids: set[str] = {
        str(item.get("item_id"))
        for old_round in state["rounds"]
        for item in ((old_round.get("task_pack") or {}).get("items") or [])
        if item.get("item_id")
    }
    source_ids: set[str] = set()
    provenance_locators: set[str] = set()
    current_item_ids: list[str] = []
    provenance_ids: list[str] = []
    historical_provenance: list[str] = []
    for old_round in state["rounds"]:
        for old_item in ((old_round.get("task_pack") or {}).get("items") or []):
            if not isinstance(old_item, dict):
                continue
            old_source_qid = old_item.get(
                "source_qid", old_item.get("qid", old_item.get("id"))
            )
            if old_source_qid not in (None, ""):
                historical_provenance.append(str(old_source_qid).strip())
            for old_locator in (old_item.get("locator"), old_item.get("derived_from")):
                old_key = locator_key(old_locator)
                if old_key:
                    historical_provenance.append(old_key)

    previous = ordered_union(
        (profile.get("next_session") or {}).get("exclude_qids"),
        *[
            ((old_round.get("task_pack") or {}).get("next_session") or {}).get("exclude_qids")
            for old_round in state["rounds"]
        ],
        historical_provenance,
    )
    previous_exclusions = {str(value) for value in previous}
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("任务包 items 中每项必须是对象")
        raw_seq = item.get("seq", position)
        try:
            seq = int(raw_seq)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"任务包第 {position} 项的 seq 不是整数") from exc
        if seq < 1 or seq in seqs:
            raise ValueError("任务包 seq 必须是互不重复的正整数")
        seqs.add(seq)
        item["seq"] = seq

        item_id = str(item.get("item_id") or f"{state_session}-r{round_no}-i{seq}").strip()
        if not item_id or item_id in item_ids:
            raise ValueError("任务包 item_id 缺失，或与当前/历史轮次重复")
        item_ids.add(item_id)
        current_item_ids.append(item_id)
        item["item_id"] = item_id

        source = item.get("source")
        if source not in SOURCES:
            raise ValueError(f"任务包 seq {seq} 的 source 必须是 workbook、variant 或 generated")
        if item.get("part") not in PARTS:
            raise ValueError(f"任务包 seq {seq} 的 part 无效")
        if item.get("ability") not in ABILITIES:
            raise ValueError(f"任务包 seq {seq} 的 ability 无效")
        if normalized.get("bank_available") is False and source != "generated":
            raise ValueError("bank_available=false 时所有题目 source 必须是 generated")

        if "repeat_for_correction" in item and not isinstance(item["repeat_for_correction"], bool):
            raise ValueError(f"任务包 seq {seq} 的 repeat_for_correction 必须是布尔值")
        repeat_for_correction = item.get("repeat_for_correction") is True
        if repeat_for_correction and not str(item.get("repeat_reason", "")).strip():
            raise ValueError(f"任务包 seq {seq} 复练时必须填写 repeat_reason")

        locator = item.get("locator")
        derived_from = item.get("derived_from")
        if source == "workbook" and not isinstance(locator, dict):
            raise ValueError(f"任务包 seq {seq} 的教辅原题必须有 locator")
        if source == "workbook" and not locator.get("unit"):
            raise ValueError(f"任务包 seq {seq} 的 locator.unit 缺失")
        if source == "variant":
            if not isinstance(derived_from, dict):
                raise ValueError(f"任务包 seq {seq} 的变式题必须有 derived_from")
            if locator not in (None, {}):
                raise ValueError(f"任务包 seq {seq} 的变式题使用 derived_from，不得同时写 locator")
            if not derived_from.get("unit"):
                raise ValueError(f"任务包 seq {seq} 的 derived_from.unit 缺失")
            if item.get("self_check_passed") is not True or item.get("in_scope") is not True:
                raise ValueError(f"任务包 seq {seq} 的变式题必须通过 self_check_passed 与 in_scope")
        if source == "generated":
            if locator not in (None, {}) or derived_from not in (None, {}):
                raise ValueError(f"任务包 seq {seq} 的生成题不得编造 locator 或 derived_from")
            if item.get("self_check_passed") is not True or item.get("in_scope") is not True:
                raise ValueError(f"任务包 seq {seq} 的生成题必须通过 self_check_passed 与 in_scope")

        unit_candidates = [
            str(value)
            for value in (
                item.get("unit"),
                locator.get("unit") if isinstance(locator, dict) else None,
                derived_from.get("unit") if isinstance(derived_from, dict) else None,
            )
            if value not in (None, "")
        ]
        if len(set(unit_candidates)) > 1:
            raise ValueError(f"任务包 seq {seq} 的 item/locator/derived_from 单元互相冲突")
        unit = unit_candidates[0] if unit_candidates else None
        if unit is None and len(session_units) == 1:
            unit = next(iter(session_units))
        if unit not in allowed_units or unit not in session_units:
            raise ValueError(f"任务包 seq {seq} 的单元超出本轮或画像范围")
        item["unit"] = unit

        source_qid = item.get("source_qid", item.get("qid", item.get("id")))
        if source_qid not in (None, ""):
            source_qid = str(source_qid).strip()
            if source_qid in source_ids:
                raise ValueError(f"任务包来源题号重复：{source_qid}")
            source_ids.add(source_qid)
            item["source_qid"] = source_qid
            provenance_ids.append(source_qid)

        direct_locator = locator_key(locator)
        if direct_locator:
            if direct_locator in provenance_locators:
                raise ValueError(f"任务包来源定位重复：{direct_locator}")
            provenance_locators.add(direct_locator)
            provenance_ids.append(direct_locator)

        derived_locator = locator_key(derived_from)
        if derived_locator:
            if derived_locator in provenance_locators:
                raise ValueError(f"任务包来源定位重复：{derived_locator}")
            provenance_locators.add(derived_locator)
            provenance_ids.append(derived_locator)

        if source in ("workbook", "variant") and not repeat_for_correction:
            repeated = [
                value
                for value in (source_qid, direct_locator, derived_locator)
                if value and value in previous_exclusions
            ]
            if repeated:
                raise ValueError(
                    f"任务包 seq {seq} 重复使用已排除的教辅原题或同源变式；"
                    "只有订正复练才可设置 repeat_for_correction=true 并说明 repeat_reason"
                )

        answer_spec = item.get("acceptable_answers")
        if answer_spec in (None, "", [], {}):
            raise ValueError(f"任务包 seq {seq} 缺少 acceptable_answers")
        if not isinstance(answer_spec, (list, dict)):
            raise ValueError(f"任务包 seq {seq} 的 acceptable_answers 必须是数组或小题对象")

    normalized["current_used_item_ids"] = current_item_ids
    next_session = normalized.setdefault("next_session", {})
    if not isinstance(next_session, dict):
        raise ValueError("任务包 next_session 必须是对象")
    next_session["exclude_qids"] = ordered_union(
        previous,
        next_session.get("exclude_qids"),
        provenance_ids,
        current_item_ids,
    )
    return normalized


def handle_init(args: argparse.Namespace) -> dict:
    path = Path(args.state)
    if path.exists() and not args.force:
        raise ValueError("状态文件已存在；若确实要新建，请换路径或显式使用 --force")
    learner = load_object(Path(args.learner), "learner") if args.learner else {}
    state = new_state(learner)
    save_state(path, state)
    return {**status_payload(state), "state": str(path)}


def handle_status(args: argparse.Namespace) -> dict:
    return status_payload(load_state(Path(args.state)))


def handle_adopt_profile(args: argparse.Namespace) -> dict:
    path = Path(args.state)
    state = load_state(path)
    value = load_object(Path(args.profile), "profile")
    if "rounds" in value and isinstance(value.get("profile"), dict):
        state["learner"] = {**state["learner"], **copy.deepcopy(value.get("learner") or {})}
        value = value["profile"]
    if not profile_is_ready(value):
        raise ValueError("接入画像缺少有效 strategy.code，或明确显示没有有效作答")
    if state["rounds"]:
        raise ValueError("已有学习轮次时不能替换画像；请新建状态或先完成当前闭环")
    state["profile"] = copy.deepcopy(value)
    if value.get("session_id"):
        state["session_id"] = str(value["session_id"])
    save_state(path, state)
    return {**status_payload(state), "profile": state["profile"]}


def prepare_diagnostic_session(state: dict, raw_session: dict) -> tuple[dict, dict]:
    """Fill diagnostic defaults from learner intake without overriding explicit input."""
    session = copy.deepcopy(raw_session)
    combined_learner = copy.deepcopy(state.get("learner") or {})
    if isinstance(session.get("learner"), dict):
        combined_learner.update(copy.deepcopy(session["learner"]))
    session["learner"] = combined_learner
    session.setdefault("session_id", state["session_id"])
    session.setdefault("generated_at", now_iso())

    session_profile = session.setdefault("profile", {})
    if not isinstance(session_profile, dict):
        raise ValueError("diagnostic session.profile 必须是对象")
    for key in ("available_minutes", "feedback_preference"):
        if session_profile.get(key) in (None, "") and combined_learner.get(key) not in (None, ""):
            session_profile[key] = copy.deepcopy(combined_learner[key])
        elif session_profile.get(key) not in (None, ""):
            combined_learner[key] = copy.deepcopy(session_profile[key])

    scope = session.setdefault("scope", {})
    if not isinstance(scope, dict):
        raise ValueError("diagnostic session.scope 必须是对象")
    if not scope.get("units") and combined_learner.get("learned_units"):
        scope["units"] = copy.deepcopy(combined_learner["learned_units"])
    units = scope.get("units")
    if not isinstance(units, list) or not units:
        raise ValueError("diagnostic session.scope.units 必须是非空数组")
    normalized_units = {str(unit) for unit in units}
    if not normalized_units.issubset(DEMO_UNITS):
        raise ValueError("诊断范围只允许 Unit 1–2")
    learned_units = combined_learner.get("learned_units")
    if isinstance(learned_units, list) and learned_units:
        if not normalized_units.issubset({str(unit) for unit in learned_units}):
            raise ValueError("诊断范围包含 learner.learned_units 之外的未学单元")
    else:
        combined_learner["learned_units"] = copy.deepcopy(units)
    return session, combined_learner


def handle_diagnose(args: argparse.Namespace) -> dict:
    path = Path(args.state)
    state = load_state(path)
    if state["rounds"]:
        raise ValueError("已有学习轮次，不能在同一状态中覆盖诊断画像")
    session, combined_learner = prepare_diagnostic_session(
        state, load_object(Path(args.session), "diagnostic session")
    )
    result = profile_engine.evaluate(session)
    state["profile"] = result
    if result.get("session_id"):
        state["session_id"] = str(result["session_id"])
    state["learner"] = combined_learner
    save_state(path, state)
    return {
        "ok": True,
        "phase": phase_of(state),
        "profile": result,
        "gradable_items": ((result.get("characteristics") or {}).get("evidence") or {}).get("item_count"),
    }


def handle_rediagnose(args: argparse.Namespace) -> dict:
    path = Path(args.state)
    state = load_state(path)
    if not profile_is_ready(state.get("profile")):
        raise ValueError("尚无有效旧画像；首次摸底请使用 diagnose")
    if phase_of(state) != "ready_for_task":
        raise ValueError("重测前必须完成待作答任务并生成报告")
    session, combined_learner = prepare_diagnostic_session(
        state, load_object(Path(args.session), "diagnostic session")
    )
    result = profile_engine.evaluate(session)
    if not profile_is_ready(result):
        raise ValueError("本次重测没有有效可评分作答，旧策略保持不变")
    before = copy.deepcopy(state["profile"])
    result.setdefault("next_session", {})["exclude_qids"] = ordered_union(
        (before.get("next_session") or {}).get("exclude_qids"),
        (result.get("next_session") or {}).get("exclude_qids"),
    )
    state["strategy_history"].append(
        {
            "replaced_at": now_iso(),
            "strategy": copy.deepcopy(before.get("strategy")),
            "profile": before,
        }
    )
    state["profile"] = result
    state["learner"] = combined_learner
    save_state(path, state)
    return {
        "ok": True,
        "phase": phase_of(state),
        "previous_strategy": (before.get("strategy") or {}).get("code"),
        "current_strategy": (result.get("strategy") or {}).get("code"),
        "profile": result,
    }


def handle_append_pack(args: argparse.Namespace) -> dict:
    path = Path(args.state)
    state = load_state(path)
    current_phase = phase_of(state)
    continue_before_report = bool(getattr(args, "continue_before_report", False))
    if current_phase == "ready_for_report" and not continue_before_report:
        raise ValueError(
            "已有一轮真实作答，正在等待学生选择；只有学生明确选择继续学习后，"
            "才可显式使用 --continue-before-report"
        )
    if current_phase not in ("ready_for_task", "ready_for_report"):
        raise ValueError(f"当前 phase={current_phase}，不能追加新任务包")
    pack = load_object(Path(args.pack), "task pack")
    round_no = len(state["rounds"]) + 1
    normalized = normalize_pack(pack, state, round_no)
    state["rounds"].append(
        {
            "round": round_no,
            "pushed_at": now_iso(),
            "task_pack": normalized,
            "raw_log": None,
            "normalized_log": None,
            "intake": {"repairs": [], "issues": []},
            "reported_at": None,
            "report_id": None,
        }
    )
    save_state(path, state)
    return {
        "ok": True,
        "phase": phase_of(state),
        "round": round_no,
        "items": len(normalized["items"]),
        "item_ids": normalized["current_used_item_ids"],
    }


def pending_round(state: dict) -> dict:
    target = next(
        (
            round_data
            for round_data in reversed(state["rounds"])
            if round_data.get("task_pack") and not round_data.get("normalized_log")
        ),
        None,
    )
    if target is None:
        raise ValueError("没有等待作答的任务轮次；先 append-pack")
    return target


def handle_append_log(args: argparse.Namespace) -> tuple[dict, int]:
    path = Path(args.state)
    state = load_state(path)
    target = pending_round(state)
    raw = load_json(Path(args.log))
    target["raw_log"] = copy.deepcopy(raw)
    try:
        normalized, repairs, issues = intake.normalize(target["task_pack"], raw)
    except Exception as exc:
        target["normalized_log"] = None
        target["intake"] = {"repairs": [], "issues": [str(exc)]}
        save_state(path, state)
        return (
            {
                "ok": False,
                "visibility": "internal",
                "phase": phase_of(state),
                "error": str(exc),
            },
            2,
        )

    target["intake"] = {"repairs": repairs, "issues": issues}
    if issues:
        target["normalized_log"] = None
        target["intake"]["normalized_candidate"] = normalized
        save_state(path, state)
        return (
            {
                "ok": False,
                "visibility": "internal",
                "phase": phase_of(state),
                "round": target["round"],
                "repairs": repairs,
                "issues": issues,
            },
            2,
        )

    target["normalized_log"] = normalized
    target["completed_at"] = now_iso()
    save_state(path, state)
    next_phase = phase_of(state)
    payload = {
        "ok": True,
        "phase": next_phase,
        "round": target["round"],
        "responses": len(normalized["responses"]),
        "repairs": repairs,
    }
    if next_phase == "ready_for_report":
        payload["round_completion_choice"] = round_completion_choice_payload()
    return (payload, 0)


def handle_apply_patch(args: argparse.Namespace) -> dict:
    path = Path(args.state)
    state = load_state(path)
    if not profile_is_ready(state.get("profile")):
        raise ValueError("没有可回写的有效画像")
    value = load_object(Path(args.patch), "profile patch")
    patch = value.get("profile_patch") if isinstance(value.get("profile_patch"), dict) else value
    observed_at = args.observed_at or value.get("generated_at") or now_iso()
    before_strategy = copy.deepcopy(state["profile"].get("strategy"))
    state["profile"] = apply_profile_patch(state["profile"], patch)
    state["profile_observations"].append(
        {
            "observed_at": observed_at,
            "source_report_id": value.get("report_id"),
            "patch": copy.deepcopy(patch),
        }
    )
    save_state(path, state)
    return {
        "ok": True,
        "phase": phase_of(state),
        "observed_at": observed_at,
        "strategy_unchanged": state["profile"].get("strategy") == before_strategy,
    }


def handle_report(args: argparse.Namespace) -> dict:
    state_path = Path(args.state)
    state = load_state(state_path)
    if phase_of(state) != "ready_for_report":
        raise ValueError(f"当前 phase={phase_of(state)}，没有可生成报告的新作答")
    if not getattr(args, "user_ended", False):
        raise ValueError(
            "学生尚未明确选择结束本次学习；先展示结束、继续、讲解三项选择。"
            "只有学生选择结束或主动要求学习总结后，才可使用 --user-ended"
        )

    target_rounds = [
        round_data
        for round_data in state["rounds"]
        if round_data.get("normalized_log") and not round_data.get("reported_at")
    ]
    pairs = [(r["task_pack"], r["normalized_log"]) for r in target_rounds]
    profile_before = copy.deepcopy(state["profile"])
    report = report_engine.build_report(pairs, profile_before)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = state.get("learner", {}).get("name")
    rendered = {
        "student.txt": compose.pad(compose.student_text(report, profile_before, name)),
        "parent.txt": compose.pad(compose.parent_text(report, profile_before, name)),
        "teacher.txt": compose.pad(compose.teacher_text(report, profile_before, name)),
    }
    report_path = out_dir / "report.json"
    atomic_write_json(report_path, report)
    for filename, content in rendered.items():
        atomic_write_text(out_dir / filename, content)

    patch = report["profile_patch"]
    state["profile"] = apply_profile_patch(state["profile"], patch)
    state["profile_observations"].append(
        {
            "observed_at": report["generated_at"],
            "source_report_id": report["report_id"],
            "patch": copy.deepcopy(patch),
        }
    )
    for round_data in target_rounds:
        round_data["reported_at"] = report["generated_at"]
        round_data["report_id"] = report["report_id"]
    metadata = {
        "report_id": report["report_id"],
        "generated_at": report["generated_at"],
        "rounds": [r["round"] for r in target_rounds],
        "out_dir": str(out_dir),
    }
    state["reports"].append(metadata)
    state["latest_report"] = metadata
    save_state(state_path, state)

    return {
        "ok": True,
        "phase": phase_of(state),
        "report_id": report["report_id"],
        "rounds": metadata["rounds"],
        "profile_updated": True,
        "written": [str(report_path)] + [str(out_dir / name) for name in rendered],
    }


def integration_checks() -> dict:
    checks: dict[str, bool] = {}

    skill_root = Path(__file__).resolve().parent.parent
    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    copy_policy_text = (skill_root / "references" / "copy-policy.md").read_text(encoding="utf-8")
    diagnostic_text = (skill_root / "references" / "diagnostic-workflow.md").read_text(encoding="utf-8")
    scope_digest_text = (skill_root / "assets" / "scope-digest.md").read_text(encoding="utf-8")
    task_workflow_text = (skill_root / "references" / "task-workflow.md").read_text(encoding="utf-8")
    checks["silent_user_facing_policy"] = all(
        marker in skill_text + "\n" + copy_policy_text
        for marker in (
            "整个对话",
            "内部执行必须静默",
            "报告流程清楚了。现在跑报告命令",
            "这句话是在帮助学生学习，还是在描述系统怎样工作",
        )
    )
    checks["free_reading_topic_policy"] = all(
        marker in skill_text + "\n" + diagnostic_text + "\n" + scope_digest_text + "\n" + copy_policy_text
        for marker in (
            "阅读主题由模型自由构思",
            "不设主题池",
            "90–120",
            "session_id",
            "不得复述、缩写或轻微改写",
            "候选主题",
        )
    )
    checks["round_completion_choice_policy"] = all(
        marker in skill_text + "\n" + copy_policy_text + "\n" + task_workflow_text
        for marker in (
            "结束本次学习",
            "继续学习",
            "还有没弄明白的题",
            "回复 1、2 或 3",
            "不得自动生成报告",
            "讲解不倒改原始作答",
        )
    )

    prepared, prepared_learner = prepare_diagnostic_session(
        new_state(
            {
                "name": "建档同学",
                "available_minutes": 15,
                "feedback_preference": "先给思路",
                "learned_units": ["Unit 1"],
            }
        ),
        {"responses": []},
    )
    checks["learner_intake_propagates"] = (
        prepared["profile"]["available_minutes"] == 15
        and prepared["profile"]["feedback_preference"] == "先给思路"
        and prepared["scope"]["units"] == ["Unit 1"]
        and prepared_learner["name"] == "建档同学"
    )
    explicit_prepared, explicit_learner = prepare_diagnostic_session(
        new_state({"available_minutes": 15}),
        {
            "profile": {"available_minutes": 25},
            "scope": {"units": ["Unit 2"]},
            "responses": [],
        },
    )
    checks["diagnostic_explicit_values_win"] = (
        explicit_prepared["profile"]["available_minutes"] == 25
        and explicit_learner["available_minutes"] == 25
        and explicit_learner["learned_units"] == ["Unit 2"]
    )

    anchor_qid = profile_engine.load_bank()[0]["id"]
    null_session = {
        "scope": {"units": ["Unit 1", "Unit 2"]},
        "responses": [{"qid": anchor_qid, "response": None}],
    }
    null_profile = profile_engine.evaluate(null_session)
    checks["diagnostic_null_not_scored"] = (
        null_profile["characteristics"]["evidence"]["item_count"] == 0
        and null_profile["next_session"]["exclude_qids"] == []
        and null_profile["strategy"]["confidence"] == "low"
    )

    ungradable_session = {
        "scope": {"units": ["Unit 1"]},
        "questions": [
            {
                "qid": "ungradable-demo",
                "unit": "Unit 1",
                "part": "基础夯实",
                "ability": "汉英转换",
                "score_eligible": False,
            }
        ],
        "responses": [
            {
                "qid": "ungradable-demo",
                "response": "",
                "model_judgment": {"status": "ungradable", "reason": "题意不足"},
            }
        ],
    }
    ungradable = profile_engine.evaluate(ungradable_session)
    checks["ungradable_item_skipped"] = (
        ungradable["characteristics"]["evidence"]["item_count"] == 0
    )

    pack = {
        "items": [
            {
                "seq": 1,
                "item_id": "demo-r1-i1",
                "acceptable_answers": ["B"],
            }
        ]
    }
    fixed, _, issues = intake.normalize(
        pack, {"responses": [{"item_id": "demo-r1-i1", "answer": "B"}]}
    )
    checks["item_id_join"] = (
        not issues
        and fixed["responses"][0]["seq"] == 1
        and fixed["responses"][0]["item_id"] == "demo-r1-i1"
    )
    _, _, empty_issues = intake.normalize(
        pack, {"responses": [{"item_id": "demo-r1-i1", "response": {}}]}
    )
    checks["empty_log_blocks_report"] = any(
        "一道题都没有作答内容" in issue for issue in empty_issues
    )

    base_profile = {
        "strategy": {"code": "B", "name": "巩固提升"},
        "mastery": {"基础夯实": 60.0, "能力提升": 50.0, "by_ability": {"语法辨析": 40.0}},
        "characteristics": {"misconceptions": ["旧观察"]},
        "next_session": {"exclude_qids": ["old"]},
    }
    merged = apply_profile_patch(
        base_profile,
        {
            "mastery_observed": {"基础夯实": None, "能力提升": 75.0},
            "by_ability_observed": {"语法辨析": 80.0},
            "misconceptions_observed": ["新观察"],
            "cumulative_exclude_item_ids": ["old", "new"],
        },
    )
    checks["profile_patch_merge"] = (
        merged["mastery"]["基础夯实"] == 60.0
        and merged["mastery"]["能力提升"] == 75.0
        and merged["next_session"]["exclude_qids"] == ["old", "new"]
        and merged["strategy"] == base_profile["strategy"]
    )

    public_pack = {
        "session_id": "public-copy-check",
        "bank_available": True,
        "tier": {"level": "C", "name": "稳固基础"},
        "session": {"anchor": "by + 动名词", "units": ["Unit 1"], "estimated_minutes": 5},
        "items": [
            {
                "seq": 1,
                "item_id": "public-copy-check-r1-i1",
                "source": "workbook",
                "part": "基础夯实",
                "ability": "语法辨析",
                "knowledge_point": "by + 动名词",
                "locator": {"unit": "Unit 1", "period": "第3课时", "exercise_no": "II", "question_no": 7},
                "acceptable_answers": ["B"],
            }
        ],
        "next_session": {"exclude_qids": ["Unit 1/第3课时/II/7"]},
    }
    public_report = report_engine.build_report(
        [(public_pack, {"responses": [{"seq": 1, "item_id": "public-copy-check-r1-i1", "response": "A"}]})],
        base_profile,
    )
    public_text = compose.student_text(public_report, base_profile, "演示同学") + compose.parent_text(
        public_report, base_profile, "演示同学"
    )
    checks["public_copy_unit_only"] = (
        "Unit 1" in public_text
        and "课时" not in public_text
        and "第 7 题" not in public_text
        and not any(route in public_text for route in ("拓展挑战", "巩固提升", "稳固基础"))
    )
    checks["public_copy_no_product_terms"] = not any(
        term in public_text
        for term in ("画像", "置信度", "qid", "item_id", "脚本", "模型判题")
    )

    # Real CLI handlers: state -> profile -> task -> item_id log -> report -> patch.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_path = root / "state.json"
        learner_path = root / "learner.json"
        profile_path = root / "profile.json"
        pack_path = root / "pack.json"
        log_path = root / "log.json"
        out_dir = root / "report-out"
        atomic_write_json(learner_path, {"name": "演示同学", "available_minutes": 15})
        handle_init(
            argparse.Namespace(state=str(state_path), learner=str(learner_path), force=False)
        )
        end_to_end_profile = {
            "session_id": load_state(state_path)["session_id"],
            "scope": {"units": ["Unit 1"]},
            "profile": {"available_minutes": 15, "feedback_preference": "先给思路"},
            "mastery": {"基础夯实": 50.0, "能力提升": None, "by_ability": {"语法辨析": 50.0}},
            "characteristics": {
                "strengths": [],
                "weaknesses": ["语法辨析"],
                "error_pattern": "知识型",
                "misconceptions": [],
                "evidence": {"item_count": 4, "correct": 2},
            },
            "strategy": {"code": "B", "name": "巩固提升", "confidence": "medium", "reason": "回归测试"},
            "next_session": {"focus_abilities": ["语法辨析"], "focus_units": ["Unit 1"], "exclude_qids": []},
        }
        atomic_write_json(profile_path, end_to_end_profile)
        handle_adopt_profile(
            argparse.Namespace(state=str(state_path), profile=str(profile_path))
        )
        atomic_write_json(
            pack_path,
            {
                "bank_available": False,
                "workbook": "《2026 初中53同步》英语九年级",
                "session": {"anchor": "by + 动名词", "units": ["Unit 1"], "estimated_minutes": 5},
                "items": [
                    {
                        "seq": 1,
                        "source": "generated",
                        "part": "基础夯实",
                        "ability": "语法辨析",
                        "knowledge_point": "by + 动名词",
                        "acceptable_answers": ["B"],
                        "self_check_passed": True,
                        "in_scope": True,
                    }
                ],
                "next_session": {"exclude_qids": []},
            },
        )

        bad_scope = load_object(pack_path, "task pack")
        bad_scope["session"]["units"] = ["Unit 9"]
        try:
            normalize_pack(bad_scope, load_state(state_path), 1)
        except ValueError as exc:
            checks["task_scope_guard"] = "超出画像" in str(exc)
        else:
            checks["task_scope_guard"] = False

        bad_enum = load_object(pack_path, "task pack")
        bad_enum["items"][0].pop("ability")
        try:
            normalize_pack(bad_enum, load_state(state_path), 1)
        except ValueError as exc:
            checks["task_enum_guard"] = "ability" in str(exc)
        else:
            checks["task_enum_guard"] = False

        source_guard_state = load_state(state_path)
        source_guard_state["profile"]["next_session"]["exclude_qids"] = [
            "Unit 1/第3课时/II/7"
        ]
        workbook_pack = {
            "bank_available": True,
            "session": {"anchor": "by + 动名词", "units": ["Unit 1"]},
            "items": [
                {
                    "seq": 1,
                    "source": "workbook",
                    "part": "基础夯实",
                    "ability": "语法辨析",
                    "locator": {
                        "unit": "Unit 1",
                        "period": "第3课时",
                        "exercise_no": "II",
                        "question_no": 7,
                    },
                    "acceptable_answers": ["B"],
                }
            ],
        }
        try:
            normalize_pack(workbook_pack, source_guard_state, 1)
        except ValueError as exc:
            duplicate_blocked = "重复使用已排除" in str(exc)
        else:
            duplicate_blocked = False
        correction_pack = copy.deepcopy(workbook_pack)
        correction_pack["items"][0]["repeat_for_correction"] = True
        correction_pack["items"][0]["repeat_reason"] = "订正上轮错题"
        correction_allowed = normalize_pack(correction_pack, source_guard_state, 1)

        conflicting_units = copy.deepcopy(workbook_pack)
        conflicting_units["items"][0]["unit"] = "Unit 1"
        conflicting_units["items"][0]["locator"]["unit"] = "Unit 9"
        try:
            normalize_pack(conflicting_units, load_state(state_path), 1)
        except ValueError as exc:
            checks["unit_field_conflict_guard"] = "单元互相冲突" in str(exc)
        else:
            checks["unit_field_conflict_guard"] = False

        variant_pack = {
            "bank_available": True,
            "session": {"anchor": "by + 动名词", "units": ["Unit 1"]},
            "items": [
                {
                    "seq": 1,
                    "source": "variant",
                    "part": "基础夯实",
                    "ability": "语法辨析",
                    "derived_from": {
                        "unit": "Unit 1",
                        "period": "第3课时",
                        "exercise_no": "II",
                        "question_no": 7,
                    },
                    "acceptable_answers": ["B"],
                    "self_check_passed": True,
                    "in_scope": True,
                }
            ],
        }
        try:
            normalize_pack(variant_pack, source_guard_state, 1)
        except ValueError as exc:
            variant_duplicate_blocked = "同源变式" in str(exc)
        else:
            variant_duplicate_blocked = False

        unreported_guard_state = load_state(state_path)
        unreported_guard_state["rounds"] = [
            {
                "round": 1,
                "task_pack": {
                    "items": [
                        {
                            "item_id": "already-used-item",
                            "source_qid": "old-qid",
                            "locator": {
                                "unit": "Unit 1",
                                "period": "第3课时",
                                "exercise_no": "II",
                                "question_no": 7,
                            },
                        }
                    ],
                    "next_session": {"exclude_qids": []},
                },
            }
        ]
        try:
            normalize_pack(workbook_pack, unreported_guard_state, 2)
        except ValueError as exc:
            unreported_duplicate_blocked = "重复使用已排除" in str(exc)
        else:
            unreported_duplicate_blocked = False
        checks["historical_source_guard"] = (
            duplicate_blocked
            and unreported_duplicate_blocked
            and variant_duplicate_blocked
            and correction_allowed["items"][0]["repeat_for_correction"] is True
        )

        handle_append_pack(argparse.Namespace(state=str(state_path), pack=str(pack_path)))
        generated_item_id = load_state(state_path)["rounds"][0]["task_pack"]["items"][0]["item_id"]
        atomic_write_json(
            log_path,
            {"responses": [{"item_id": generated_item_id, "response": "B", "hints_used": 0}]},
        )
        log_result, log_code = handle_append_log(
            argparse.Namespace(state=str(state_path), log=str(log_path))
        )

        duplicate_pack = load_object(pack_path, "task pack")
        duplicate_pack["items"][0]["item_id"] = generated_item_id
        duplicate_path = root / "duplicate-pack.json"
        atomic_write_json(duplicate_path, duplicate_pack)
        try:
            handle_append_pack(
                argparse.Namespace(
                    state=str(state_path),
                    pack=str(duplicate_path),
                    continue_before_report=True,
                )
            )
        except ValueError as exc:
            checks["session_wide_item_id_guard"] = "历史轮次重复" in str(exc)
        else:
            checks["session_wide_item_id_guard"] = False

        pack2 = load_object(pack_path, "task pack")
        pack2["session"]["anchor"] = "宾语从句连接词"
        pack2["items"][0]["knowledge_point"] = "宾语从句连接词"
        pack2["items"][0]["acceptable_answers"] = ["that"]
        pack2_path = root / "pack2.json"
        atomic_write_json(pack2_path, pack2)
        handle_append_pack(
            argparse.Namespace(
                state=str(state_path),
                pack=str(pack2_path),
                continue_before_report=True,
            )
        )
        second_item_id = load_state(state_path)["rounds"][1]["task_pack"]["items"][0]["item_id"]
        log2_path = root / "log2.json"
        atomic_write_json(
            log2_path,
            {"responses": [{"item_id": second_item_id, "response": "that", "hints_used": 0}]},
        )
        second_log_result, second_log_code = handle_append_log(
            argparse.Namespace(state=str(state_path), log=str(log2_path))
        )
        choice_status = status_payload(load_state(state_path))
        try:
            handle_report(
                argparse.Namespace(state=str(state_path), out_dir=str(out_dir), user_ended=False)
            )
        except ValueError as exc:
            report_without_choice_blocked = "尚未明确选择结束" in str(exc)
        else:
            report_without_choice_blocked = False
        report_result = handle_report(
            argparse.Namespace(state=str(state_path), out_dir=str(out_dir), user_ended=True)
        )
        final_state = load_state(state_path)
        student_copy = (out_dir / "student.txt").read_text(encoding="utf-8")
        report_json = load_object(out_dir / "report.json", "report")
        checks["end_to_end_cycle"] = (
            log_code == 0
            and log_result["ok"] is True
            and second_log_code == 0
            and second_log_result["ok"] is True
            and report_result["ok"] is True
            and report_json["rounds_count"] == 2
            and phase_of(final_state) == "ready_for_task"
            and len(final_state["profile_observations"]) == 1
            and final_state["profile"]["strategy"]["code"] == "B"
            and "演示同学" in student_copy
            and "课时" not in student_copy
            and not any(route in student_copy for route in ("拓展挑战", "巩固提升", "稳固基础"))
        )
        checks["round_completion_choice_gate"] = (
            report_without_choice_blocked
            and log_result.get("round_completion_choice", {}).get("required") is True
            and second_log_result.get("round_completion_choice", {}).get("required") is True
            and choice_status.get("round_completion_choice", {}).get("required") is True
            and [
                choice.get("code")
                for choice in choice_status["round_completion_choice"].get("choices", [])
            ]
            == ["1", "2", "3"]
        )

        anchor = profile_engine.resolve(profile_engine.load_bank()[0])
        rediagnose_path = root / "rediagnose.json"
        atomic_write_json(
            rediagnose_path,
            {
                "scope": {"units": [anchor["unit"]]},
                "responses": [
                    {
                        "qid": anchor["qid"],
                        "response": sorted(profile_engine.accepted_variants(anchor["answer_spec"]))[0],
                    }
                ],
            },
        )
        before_exclusions = copy.deepcopy(
            (final_state["profile"].get("next_session") or {}).get("exclude_qids")
        )
        rediagnosed = handle_rediagnose(
            argparse.Namespace(state=str(state_path), session=str(rediagnose_path))
        )
        after_rediagnose = load_state(state_path)
        checks["rediagnose_cycle"] = (
            rediagnosed["ok"] is True
            and len(after_rediagnose["strategy_history"]) == 1
            and set(before_exclusions).issubset(
                set((after_rediagnose["profile"].get("next_session") or {}).get("exclude_qids") or [])
            )
            and phase_of(after_rediagnose) == "ready_for_task"
        )
    return checks


def handle_validate(_: argparse.Namespace) -> dict:
    profile_result = profile_engine.validate()
    report_result = report_engine.selfcheck()
    integration = integration_checks()
    ok = profile_result.get("ok") is True and report_result.get("ok") is True and all(integration.values())
    return {
        "ok": ok,
        "profile_engine": {
            "ok": profile_result.get("ok"),
            "resolved": profile_result.get("resolved"),
            "custom_qid_contract": profile_result.get("custom_qid_contract"),
            "errors": profile_result.get("errors"),
        },
        "report_engine": report_result,
        "integration": integration,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="缤果学伴完整学习闭环状态机")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init", help="新建学习状态")
    init_cmd.add_argument("--state", required=True)
    init_cmd.add_argument("--learner")
    init_cmd.add_argument("--force", action="store_true")

    status_cmd = sub.add_parser("status", help="查看下一阶段")
    status_cmd.add_argument("--state", required=True)

    adopt_cmd = sub.add_parser("adopt-profile", help="接入已有画像")
    adopt_cmd.add_argument("--state", required=True)
    adopt_cmd.add_argument("--profile", required=True)

    diagnose_cmd = sub.add_parser("diagnose", help="判定摸底画像并写入状态")
    diagnose_cmd.add_argument("--state", required=True)
    diagnose_cmd.add_argument("--session", required=True)

    rediagnose_cmd = sub.add_parser("rediagnose", help="完成当前闭环后重新摸底并更新策略")
    rediagnose_cmd.add_argument("--state", required=True)
    rediagnose_cmd.add_argument("--session", required=True)

    pack_cmd = sub.add_parser("append-pack", help="校验任务包并追加新一轮")
    pack_cmd.add_argument("--state", required=True)
    pack_cmd.add_argument("--pack", required=True)
    pack_cmd.add_argument(
        "--continue-before-report",
        action="store_true",
        help="已有真实作答但需在同次学习继续一轮时显式使用",
    )

    log_cmd = sub.add_parser("append-log", help="归一化并回填一轮真实作答")
    log_cmd.add_argument("--state", required=True)
    log_cmd.add_argument("--log", required=True)

    report_cmd = sub.add_parser("report", help="生成三类报告并自动回写画像")
    report_cmd.add_argument("--state", required=True)
    report_cmd.add_argument("--out-dir", required=True)
    report_cmd.add_argument(
        "--user-ended",
        action="store_true",
        help="仅在学生明确选择结束本次学习或主动要求学习总结后使用",
    )

    patch_cmd = sub.add_parser("apply-patch", help="显式合并已有报告补丁")
    patch_cmd.add_argument("--state", required=True)
    patch_cmd.add_argument("--patch", required=True)
    patch_cmd.add_argument("--observed-at")

    sub.add_parser("validate", help="运行所有脚本与闭环回归测试")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    handlers = {
        "init": handle_init,
        "status": handle_status,
        "adopt-profile": handle_adopt_profile,
        "diagnose": handle_diagnose,
        "rediagnose": handle_rediagnose,
        "append-pack": handle_append_pack,
        "append-log": handle_append_log,
        "report": handle_report,
        "apply-patch": handle_apply_patch,
        "validate": handle_validate,
    }
    try:
        result = handlers[args.command](args)
        if isinstance(result, tuple):
            payload, exit_code = result
            emit(payload)
            raise SystemExit(exit_code)
        emit(result)
        if args.command == "validate" and not result.get("ok"):
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        emit({"ok": False, "visibility": "internal", "error": str(exc)})
        raise SystemExit(2)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户画像建模引擎 —— 确定性产出学习者画像与 A/B/C 策略判定。

  python profile_engine.py anchors --units "Unit 1" "Unit 2" --count 3
  python profile_engine.py evaluate --input session.json
  python profile_engine.py validate

判定必须由本脚本给出，LLM 只负责提问与讲解。同一份作答必须得到同一结果。
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def configure_utf8_stdio() -> None:
    """Keep Chinese CLI output readable on Windows without requiring -X utf8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


configure_utf8_stdio()

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = SKILL_DIR / "assets"
BANK_PATH = ASSETS_DIR / "anchor_bank.json"
# 本 skill 自包含：教辅题库内置于 assets/questions/，不引用项目其他目录。

# 教辅题型 -> 五类能力维度
ABILITY_MAP = {
    "用括号内所给词的适当形式填空": "词形变化",
    "根据句意和首字母提示写出所缺的单词": "词形变化",
    "从方框中选出合适的词，并用其适当形式填空": "词形变化",
    "从方框中选出合适的词或短语，并用其适当形式填空": "词形变化",
    "根据汉语意思完成句子，每空一词": "汉英转换",
    "按要求完成句子，每空一词": "汉英转换",
    "单项选择": "语法辨析",
    "语法填空": "语法辨析",
    "阅读理解": "篇章理解",
    "完形填空": "篇章理解",
    "任务型阅读": "篇章理解",
    "任务型阅读（选句填空）": "篇章理解",
    "短文填空": "篇章理解",
    "短文还原": "篇章理解",
    "补全对话": "篇章理解",
}
ABILITIES = ["词形变化", "汉英转换", "语法辨析", "篇章理解"]
PARTS = ["基础夯实", "能力提升"]

STRATEGIES = {
    "A": {"name": "拓展挑战",
          "mix": {"基础夯实": 30, "能力提升": 70},
          "style": "启发式，以追问和变式为主"},
    "B": {"name": "巩固提升",
          "mix": {"基础夯实": 60, "能力提升": 40},
          "style": "先给思路再分步展开"},
    "C": {"name": "稳固基础",
          "mix": {"基础夯实": 85, "能力提升": 15},
          "style": "概念和例题优先，陪伴式反馈"},
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# ---------------------------------------------------------------- 回源解析
def resolve(item: dict) -> dict:
    """从教辅 JSON 解析题干、标准答案与画像元数据。"""
    data = load_json(ASSETS_DIR / item["source_file"])
    loc = item["locator"]
    for section in data["sections"]:
        if section.get("period") != loc["period"]:
            continue
        for ex in section.get("exercises", []):
            if ex.get("exercise_no") != loc["exercise_no"]:
                continue
            for q in ex.get("questions", []):
                if q.get("no") != loc["question_no"]:
                    continue
                etype = ex.get("type", "")
                if etype not in ABILITY_MAP:
                    raise ValueError(f"题型未纳入能力映射: {etype}")
                answer_map = ex.get("answer")
                answer_key = str(loc["question_no"])
                if not isinstance(answer_map, dict) or answer_key not in answer_map:
                    raise ValueError(
                        f"题库缺少标准答案: {item['id']} -> answer[{answer_key}]"
                    )
                return {
                    "qid": item["id"],
                    "unit": item["unit"],
                    "period": loc["period"],
                    "part": ex.get("part") or "基础夯实",
                    "type": etype,
                    "ability": ABILITY_MAP[etype],
                    "knowledge_point": item.get("knowledge_point"),
                    "stem": q.get("stem"),
                    "options": q.get("options"),
                    "chinese": q.get("chinese"),
                    "given_word": q.get("given_word"),
                    "answer_spec": answer_map[answer_key],
                    "provenance": "anchor",
                }
    raise ValueError(f"锚题定位失败: {item['id']} -> {loc}")


def load_bank() -> list[dict]:
    return load_json(BANK_PATH)["items"]


# ------------------------------------------------------------------ anchors
def anchors(units: list[str], count: int) -> dict:
    """挑选锚题。按能力维度轮转，尽量分散，保证难度校准点有代表性。"""
    pool = [resolve(i) for i in load_bank() if i["unit"] in units]
    by_ability: dict[str, list] = defaultdict(list)
    for q in pool:
        by_ability[q["ability"]].append(q)

    picked, seen = [], set()
    while len(picked) < min(count, len(pool)):
        progressed = False
        for ability in ABILITIES:
            if len(picked) >= count:
                break
            for q in by_ability.get(ability, []):
                if q["qid"] not in seen:
                    picked.append(q)
                    seen.add(q["qid"])
                    progressed = True
                    break
        if not progressed:
            break

    return {
        "requested": count,
        "selected": len(picked),
        "pool_size": len(pool),
        "items": [
            {
                k: v
                for k, v in q.items()
                if k not in {"answer_spec", "period"}
            }
            for q in picked
        ],
        "note": "答案不在此输出。收齐作答后再交给 evaluate 判分。",
    }


# ----------------------------------------------------------------- evaluate
def normalize_text(value: Any) -> str:
    """Normalize English responses without collapsing word boundaries."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"[，,；;：:。.!！?？'\"`（）()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def expand_answer_piece(value: str) -> list[str]:
    """Expand slash alternatives inside one answer slot."""
    value = value.strip()
    if re.search(r"\s/\s", value):
        return [part.strip() for part in re.split(r"\s/\s", value) if part.strip()]
    choices: list[list[str]] = []
    for token in value.split():
        choices.append([part for part in token.split("/") if part])
    return [" ".join(parts) for parts in itertools.product(*choices)] if choices else []


def accepted_variants(answer_spec: Any) -> set[str]:
    """Convert source or generated answer specifications into comparable variants."""
    if isinstance(answer_spec, dict):
        if "acceptable_answers" in answer_spec:
            return accepted_variants(answer_spec["acceptable_answers"])
        if answer_spec.get("mode") == "slots":
            slots = answer_spec.get("slots", [])
            expanded_slots = [
                [str(choice) for choice in slot]
                if isinstance(slot, list)
                else [str(slot)]
                for slot in slots
            ]
            return {
                normalize_text(" ".join(parts))
                for parts in itertools.product(*expanded_slots)
            }
        raise ValueError("无法识别的答案对象，请使用 acceptable_answers 或 mode=slots")

    if isinstance(answer_spec, list):
        variants: set[str] = set()
        for value in answer_spec:
            variants.update(accepted_variants(value))
        return variants

    raw = str(answer_spec).strip()
    if not raw:
        return set()
    variants = {normalize_text(raw)}

    # 选择题答案常写成 "B. by"；同时接受 B、by 和完整形式。
    option_match = re.match(r"^\s*([A-Da-d])\s*[.、:：)]\s*(.+)$", raw)
    if option_match:
        variants.add(normalize_text(option_match.group(1)))
        variants.add(normalize_text(option_match.group(2)))

    # 分号分隔填空槽，斜杠表示该槽可接受多个答案。
    slots = [slot.strip() for slot in re.split(r"[;；]", raw) if slot.strip()]
    if slots:
        expanded_slots = [expand_answer_piece(slot) for slot in slots]
        if all(expanded_slots):
            variants.update(
                normalize_text(" ".join(parts))
                for parts in itertools.product(*expanded_slots)
            )
    return {value for value in variants if value}


def response_is_correct(response: Any, answer_spec: Any) -> bool:
    return normalize_text(response) in accepted_variants(answer_spec)


def response_is_answered(response: Any) -> bool:
    """Only real learner input may enter score, confidence, or exclusion counts."""
    if response is None:
        return False
    if isinstance(response, str):
        return bool(response.strip())
    if isinstance(response, dict):
        return any(response_is_answered(value) for value in response.values())
    if isinstance(response, (list, tuple, set)):
        return any(response_is_answered(value) for value in response)
    return True


def normalize_qid(value: Any) -> str:
    """Use one representation for string and numeric custom question ids."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def item_answer_spec(item: dict, qid: str, allow_model_fallback: bool = False) -> Any:
    """Read the standard answer from canonical or common question fields."""
    for key in ("acceptable_answers", "answers", "answer"):
        if key in item:
            answer_spec = item[key]
            if accepted_variants(answer_spec):
                return answer_spec
            raise ValueError(f"{qid}: {key} 为空")
    if allow_model_fallback:
        return None
    raise ValueError(
        f"{qid}: 缺少标准答案；请提供 acceptable_answers（也兼容 answers 或 answer）"
    )


def question_list(value: Any, source: str) -> list[dict]:
    """Normalize a top-level custom-question collection."""
    if value is None:
        return []
    if isinstance(value, dict) and "items" in value:
        value = value["items"]
    if not isinstance(value, list):
        raise ValueError(f"{source} 必须是题目数组，或包含 items 数组的对象")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{source} 中每道题必须是对象")
    return value


def custom_item_specs(session: dict, responses: list[dict]) -> list[tuple[str, dict]]:
    """Collect custom question definitions from stable, portable input shapes."""
    specs: list[tuple[str, dict]] = []
    for key in ("generated_items", "questions"):
        for item in question_list(session.get(key), key):
            specs.append((key, item))

    # A response may carry its own question definition. This is useful on agent
    # platforms that do not persist a separate generated_items collection.
    for record in responses:
        if not isinstance(record, dict):
            continue
        inline = record.get("question")
        if inline is not None:
            if not isinstance(inline, dict):
                raise ValueError("responses[].question 必须是对象")
            item = dict(inline)
            item.setdefault("qid", record.get("qid"))
            specs.append(("responses[].question", item))
        elif any(key in record for key in ("acceptable_answers", "answers", "answer")):
            # Also accept a flat, self-contained response record. `response` is
            # always the learner's answer; answer/answers are the answer key.
            specs.append(("responses[]", record))
    return specs


def response_model_judgment(record: dict) -> dict | None:
    """Validate an internal model fallback attached to one learner response."""
    judgment = record.get("model_judgment")
    if judgment is None:
        return None
    qid = normalize_qid(record.get("qid")) or "<missing-qid>"
    if not isinstance(judgment, dict):
        raise ValueError(f"{qid}: model_judgment 必须是对象")
    if judgment.get("status") == "ungradable":
        if not str(judgment.get("reason", "")).strip():
            raise ValueError(f"{qid}: ungradable 判断必须说明 reason")
        return judgment
    if not isinstance(judgment.get("correct"), bool):
        raise ValueError(f"{qid}: model_judgment.correct 必须是布尔值")
    if not str(judgment.get("reason", "")).strip():
        raise ValueError(f"{qid}: model_judgment.reason 不能为空")
    return judgment


def generated_item_index(session: dict, responses: list[dict]) -> dict[str, dict]:
    """Validate runtime-generated questions before they affect the profile."""
    result: dict[str, dict] = {}
    sources: dict[str, str] = {}
    fallback_qids = {
        normalize_qid(record.get("qid"))
        for record in responses
        if isinstance(record, dict) and response_model_judgment(record) is not None
    }
    for source, item in custom_item_specs(session, responses):
        qid = normalize_qid(item.get("qid", item.get("id")))
        if not qid:
            raise ValueError(f"{source} 中存在缺失的 qid（也兼容 id）")
        etype = item.get("type")
        ability = item.get("ability") or ABILITY_MAP.get(etype)
        if item.get("part") not in PARTS:
            raise ValueError(f"{qid}: part 必须是基础夯实或能力提升")
        if ability not in ABILITIES:
            raise ValueError(f"{qid}: 无效 ability -> {ability}")
        if not item.get("unit"):
            raise ValueError(f"{qid}: 缺少 unit")
        score_eligible = item.get("score_eligible", True) is not False
        answer_spec = item_answer_spec(
            item,
            qid,
            allow_model_fallback=(qid in fallback_qids or not score_eligible),
        )
        normalized = {
            "qid": qid,
            "unit": item["unit"],
            "part": item["part"],
            "type": etype,
            "ability": ability,
            "knowledge_point": item.get("knowledge_point"),
            "answer_spec": answer_spec,
            "score_eligible": score_eligible,
            "provenance": "generated",
            "misconception_map": item.get("misconception_map", {}),
        }
        if qid in result:
            if result[qid] != normalized:
                raise ValueError(
                    f"自定义 qid 定义冲突: {qid} 同时出现在 {sources[qid]} 和 {source}"
                )
            continue
        result[qid] = normalized
        sources[qid] = source
    return result


def grade_responses(session: dict) -> list[dict]:
    """Grade qid + response records using source answers or generated item specs."""
    if "responses" not in session:
        if "answers" in session:
            raise ValueError("旧字段 answers 已停用，请改为 responses: [{qid, response}]")
        raise ValueError("缺少 responses 字段")
    responses = session["responses"]
    if not isinstance(responses, list) or not responses:
        raise ValueError("responses 必须是非空数组")
    scope_units = session.get("scope", {}).get("units", [])
    if not isinstance(scope_units, list) or not scope_units:
        raise ValueError("scope.units 必须是非空数组")
    allowed_units = set(scope_units)

    item_index = {q["qid"]: q for q in (resolve(item) for item in load_bank())}
    for qid, item in generated_item_index(session, responses).items():
        if qid in item_index:
            raise ValueError(f"自定义 qid 与教辅锚题冲突: {qid}")
        item_index[qid] = item

    seen: set[str] = set()
    graded: list[dict] = []
    for record in responses:
        if not isinstance(record, dict):
            raise ValueError("responses 中每项必须是对象")
        qid = normalize_qid(record.get("qid"))
        if not qid or qid in seen:
            raise ValueError("responses 中存在缺失或重复的 qid")
        if qid not in item_index:
            raise ValueError(
                f"未知 qid: {qid}。若为自定义生成题，请把题目定义放入顶层 "
                "generated_items/questions，或放入该作答的 question 字段；定义必须包含 "
                "unit、part、type/ability；随后在内部恢复判定并重试。"
            )
        seen.add(qid)
        item = item_index[qid]
        if item["unit"] not in allowed_units:
            raise ValueError(f"{qid}: 题目单元不在 scope.units 中")
        judgment = response_model_judgment(record)
        if judgment is not None and item["provenance"] != "generated":
            raise ValueError(f"{qid}: 教辅锚题不允许使用 model_judgment")
        response = record.get("response")
        if (
            item.get("score_eligible") is False
            or (judgment is not None and judgment.get("status") == "ungradable")
            or not response_is_answered(response)
        ):
            continue
        answer_spec = item.get("answer_spec")
        deterministic_correct = (
            response_is_correct(response, answer_spec)
            if answer_spec is not None
            else False
        )
        used_model_fallback = (
            not deterministic_correct
            and judgment is not None
            and item["provenance"] == "generated"
        )
        correct = (
            bool(judgment["correct"])
            if used_model_fallback
            else deterministic_correct
        )
        misconception = None
        mapping = item.get("misconception_map", {})
        if mapping and not correct:
            misconception = mapping.get(str(response)) or mapping.get(normalize_text(response))
        graded.append(
            {
                "qid": qid,
                "unit": item["unit"],
                "part": item["part"],
                "ability": item["ability"],
                "knowledge_point": item.get("knowledge_point"),
                "correct": correct,
                "provenance": item["provenance"],
                "grading_source": "model_fallback" if used_model_fallback else "answer_key",
                "misconception": misconception,
            }
        )
    return graded


def pct(correct: int, total: int) -> float | None:
    return round(correct / total * 100, 1) if total else None


def evaluate(session: dict) -> dict:
    # 摸底不提供提示，因此不存在「协助完成」的题，全部提交记录一律计入。
    valid = grade_responses(session)

    part_stat = {p: [0, 0] for p in PARTS}
    abil_stat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    unit_stat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misc: dict[str, int] = defaultdict(int)

    for a in valid:
        part = a.get("part", "基础夯实")
        ability = a.get("ability", "语法辨析")
        ok = bool(a.get("correct"))
        for bucket, key in ((part_stat, part), (abil_stat, ability)):
            bucket[key][1] += 1
            bucket[key][0] += ok
        if a.get("unit"):
            unit_stat[a["unit"]][1] += 1
            unit_stat[a["unit"]][0] += ok
        if not ok and a.get("misconception"):
            misc[a["misconception"]] += 1

    basic = pct(part_stat["基础夯实"][0], part_stat["基础夯实"][1])
    upper = pct(part_stat["能力提升"][0], part_stat["能力提升"][1])
    total_correct = sum(1 for a in valid if a.get("correct"))

    mastery = {
        "基础夯实": basic,
        "能力提升": upper,
        "by_ability": {k: pct(v[0], v[1]) for k, v in sorted(abil_stat.items())},
    }

    # ---- ABC 判定（教辅只有两个难度层） -------------------------------
    if not valid:
        code, reason = "B", "无有效作答，给出临时策略，需补充诊断证据"
    elif basic is not None and basic < 60:
        code, reason = "C", f"基础夯实正确率 {basic}%，未达 60%"
    elif upper is not None and basic is not None and basic >= 80 and upper >= 60:
        code, reason = "A", f"基础夯实 {basic}%、能力提升 {upper}%，均达标"
    else:
        parts = []
        if basic is not None:
            parts.append(f"基础夯实 {basic}%")
        parts.append(f"能力提升 {upper}%" if upper is not None else "能力提升未测")
        code, reason = "B", "、".join(parts) + "，基础可用但提升不足"

    # ---- 强弱项 -------------------------------------------------------
    rated = {k: v for k, v in mastery["by_ability"].items() if v is not None}
    strengths = sorted([k for k, v in rated.items() if v >= 80], key=lambda k: -rated[k])
    weaknesses = sorted([k for k, v in rated.items() if v < 60], key=lambda k: rated[k])

    # ---- 错误模式 -----------------------------------------------------
    wrong = [a for a in valid if not a.get("correct")]
    if not wrong:
        pattern = None
    elif misc and max(misc.values()) >= 2:
        pattern = "知识型"
    elif basic is not None and upper is not None and basic < upper:
        pattern = "粗心型"
    elif basic is not None and basic >= 80 and upper is not None and upper < 60:
        pattern = "思路型"
    else:
        by_ab_wrong = defaultdict(int)
        for a in wrong:
            by_ab_wrong[a.get("ability")] += 1
        pattern = "知识型" if by_ab_wrong and max(by_ab_wrong.values()) >= 2 else "粗心型"

    # ---- 置信度 -------------------------------------------------------
    n = len(valid)
    covered_parts = sum(1 for p in PARTS if part_stat[p][1] > 0)
    anchors_used = sum(1 for a in valid if a.get("provenance", "anchor") == "anchor")
    model_fallbacks = sum(
        1 for a in valid if a.get("grading_source") == "model_fallback"
    )
    if n >= 8 and covered_parts == 2:
        confidence = "high"
    elif n >= 4 and covered_parts == 2:
        confidence = "medium"
    else:
        confidence = "low"
    if anchors_used == 0 and confidence == "high":
        confidence = "medium"
    if model_fallbacks and confidence == "high":
        confidence = "medium"

    # ---- 下一环节参数 -------------------------------------------------
    focus_abilities = weaknesses or sorted(rated, key=lambda k: rated[k])[:2]
    focus_units = sorted(
        (u for u, v in unit_stat.items() if v[1] and v[0] / v[1] < 1),
        key=lambda u: unit_stat[u][0] / unit_stat[u][1],
    )
    minutes = session.get("profile", {}).get("available_minutes", 20)

    prof = session.get("profile", {})
    scope = session.get("scope", {})

    return {
        "session_id": session.get("session_id"),
        "generated_at": session.get("generated_at"),
        "scope": {
            "units": scope.get("units", []),
        },
        "profile": {
            "available_minutes": minutes,
            "feedback_preference": prof.get("feedback_preference", "先给思路"),
        },
        "mastery": mastery,
        "characteristics": {
            "strengths": strengths,
            "weaknesses": weaknesses,
            "error_pattern": pattern,
            "misconceptions": [k for k, _ in sorted(misc.items(), key=lambda x: -x[1])],
            "evidence": {"item_count": n, "correct": total_correct},
        },
        "strategy": {
            "code": code,
            "name": STRATEGIES[code]["name"],
            "confidence": confidence,
            "reason": reason,
        },
        "next_session": {
            "difficulty_mix": STRATEGIES[code]["mix"],
            "feedback_style": prof.get("feedback_preference") or STRATEGIES[code]["style"],
            "item_count": max(4, min(8, minutes // 3)),
            "focus_abilities": focus_abilities,
            "focus_units": focus_units,
            "exclude_qids": [a["qid"] for a in valid],
        },
    }


# ----------------------------------------------------------------- validate
def validate() -> dict:
    errs, rows = [], []
    for item in load_bank():
        src = ASSETS_DIR / item["source_file"]
        if not src.exists():
            errs.append(f"{item['id']}: 教辅文件不存在 {item['source_file']}")
            continue
        try:
            q = resolve(item)
        except (ValueError, KeyError) as exc:
            errs.append(str(exc))
            continue
        if q["type"] not in ABILITY_MAP:
            errs.append(f"{q['qid']}: 题型未纳入能力映射 -> {q['type']}")
        if not accepted_variants(q["answer_spec"]):
            errs.append(f"{q['qid']}: 标准答案无法解析")
        rows.append({"qid": q["qid"], "unit": q["unit"], "period": q["period"],
                     "part": q["part"], "ability": q["ability"]})
    dist: dict[str, int] = defaultdict(int)
    for r in rows:
        dist[r["ability"]] += 1
    contract_checks: dict[str, bool] = {}
    custom_cases = {
        "questions_alias": {
            "scope": {"units": ["Unit 1"]},
            "questions": [{
                "id": "my-custom-qid-01",
                "unit": "Unit 1",
                "part": "基础夯实",
                "type": "单项选择",
                "answer": "B. by",
            }],
            "responses": [{"qid": "my-custom-qid-01", "response": "by"}],
        },
        "inline_question": {
            "scope": {"units": ["Unit 2"]},
            "responses": [{
                "qid": 20260812,
                "response": "that",
                "question": {
                    "unit": "Unit 2",
                    "part": "能力提升",
                    "ability": "语法辨析",
                    "answers": ["that"],
                },
            }],
        },
        "model_fallback": {
            "scope": {"units": ["Unit 1"]},
            "questions": [{
                "qid": "model-judged-qid-01",
                "unit": "Unit 1",
                "part": "基础夯实",
                "ability": "汉英转换",
            }],
            "responses": [{
                "qid": "model-judged-qid-01",
                "response": "by reading aloud",
                "model_judgment": {
                    "correct": True,
                    "reason": "语义与题目要求一致",
                },
            }],
        },
    }
    for name, session in custom_cases.items():
        try:
            graded = grade_responses(session)
            contract_checks[name] = (
                len(graded) == 1 and graded[0]["correct"] is True
            )
            if not contract_checks[name]:
                errs.append(f"自定义 qid 契约测试失败: {name}")
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            contract_checks[name] = False
            errs.append(f"自定义 qid 契约测试失败: {name} -> {exc}")
    return {"ok": not errs, "errors": errs, "resolved": len(rows),
            "ability_distribution": dict(dist),
            "custom_qid_contract": contract_checks, "items": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("anchors")
    a.add_argument("--units", nargs="+", default=["Unit 1", "Unit 2"])
    a.add_argument("--count", type=int, default=3)
    e = sub.add_parser("evaluate")
    e.add_argument("--input", required=True, help="会话 JSON 路径；- 表示从 stdin 读取")
    sub.add_parser("validate")

    args = ap.parse_args()
    try:
        if args.cmd == "anchors":
            emit(anchors(args.units, args.count))
        elif args.cmd == "evaluate":
            session = (
                json.load(sys.stdin)
                if args.input == "-"
                else load_json(Path(args.input))
            )
            emit(evaluate(session))
        else:
            r = validate()
            emit(r)
            sys.exit(0 if r["ok"] else 1)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        emit({"ok": False, "visibility": "internal", "error": str(exc)})
        sys.exit(2)


if __name__ == "__main__":
    main()

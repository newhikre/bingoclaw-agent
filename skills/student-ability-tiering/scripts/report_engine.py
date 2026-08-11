#!/usr/bin/env python3
"""缤果学伴 · 学习报告引擎。

输入 student-ability-tiering 的任务包 + 本次授课的作答记录，
输出结构化学习报告与画像回写补丁（profile_patch）。判分与统计以本脚本为准。
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PARTS = ["基础夯实", "能力提升"]
ABILITIES = ["词形变化", "汉英转换", "语法辨析", "篇章理解"]


# ----------------------------------------------------------------- 基础工具
def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ----------------------------------------------------------------- 会话文件
def append_round(path: Path, pack: dict | None, log: dict | None, round_no: int | None) -> dict:
    """把一轮推题、或这一轮做完的记录，追加进会话文件。整份读进来改完再写回，不覆盖旧轮次。"""
    if pack is None and log is None:
        raise ValueError("append 至少要给 --pack（推题）或 --log（做完回填）其中之一")

    data = load_json(path) if path.exists() else {"session_id": None, "workbook": None, "rounds": []}
    if not isinstance(data.get("rounds"), list):
        raise ValueError(f"{path.name} 里没有 rounds 数组，不是会话文件")
    rounds = data["rounds"]

    if pack is not None:
        if not data.get("session_id"):
            data["session_id"] = pack.get("session_id") or f"local-{datetime.now():%Y%m%d-%H%M%S}"
        data["workbook"] = data.get("workbook") or pack.get("workbook")
        rounds.append(
            {"round": len(rounds) + 1, "pushed_at": now_iso(), "task_pack": pack, "log": None}
        )

    if log is not None:
        if round_no is not None:
            target = next((r for r in rounds if r.get("round") == round_no), None)
            if target is None:
                raise ValueError(f"会话文件里没有第 {round_no} 轮")
        else:
            target = next((r for r in reversed(rounds) if r.get("log") is None), None)
            if target is None:
                raise ValueError("没有待回填的轮次，先 append --pack 再回填；要改已完成的轮次请指定 --round")
        target["log"] = log
        target["completed_at"] = now_iso()

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "session_file": str(path),
        "session_id": data["session_id"],
        "rounds": [{"round": r["round"], "completed": r.get("log") is not None} for r in rounds],
    }


def pairs_from_session(data: dict) -> tuple[list[tuple[dict, dict]], list[str]]:
    """会话文件 → 报告用的 (任务包, 作答记录) 列表。没做完的轮次不进报告。"""
    rounds = sorted(data.get("rounds", []), key=lambda r: r.get("round", 0))
    pairs, gaps = [], []
    for entry in rounds:
        pack = entry.get("task_pack")
        if not pack:
            raise ValueError(f"第 {entry.get('round')} 轮缺 task_pack")
        if entry.get("log"):
            pairs.append((pack, entry["log"]))
        else:
            gaps.append(f"第 {entry.get('round')} 轮已推题但没有作答记录，未计入报告")
    if not pairs:
        raise ValueError("会话文件里没有任何做完的轮次")
    return pairs, gaps


def normalize_text(value: Any) -> str:
    """与上游 profile_engine 同口径：不折叠词边界。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"[，,；;：:。.!！?？'\"`（）()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def accepted_variants(spec: Any) -> set[str]:
    """把 acceptable_answers 展开成可比较的变体集合。"""
    if isinstance(spec, list):
        variants: set[str] = set()
        for value in spec:
            variants.update(accepted_variants(value))
        return variants
    raw = str(spec).strip()
    if not raw:
        return set()
    variants = {normalize_text(raw)}
    # 选择题常写成 "B. by"，字母与选项内容都接受
    option = re.match(r"^\s*([A-Da-d])\s*[.、:：)]\s*(.+)$", raw)
    if option:
        variants.add(normalize_text(option.group(1)))
        variants.add(normalize_text(option.group(2)))
    # "loud / loudly" 这类斜杠多解
    if re.search(r"\s/\s", raw):
        variants.update(normalize_text(p) for p in re.split(r"\s/\s", raw))
    else:
        slots = [[p for p in token.split("/") if p] for token in raw.split()]
        if slots and any(len(s) > 1 for s in slots):
            variants.update(
                normalize_text(" ".join(parts)) for parts in itertools.product(*slots)
            )
    return {v for v in variants if v}


def is_correct(response: Any, spec: Any) -> bool:
    return normalize_text(response) in accepted_variants(spec)


def pct(part: int, total: int) -> float | None:
    return round(part / total * 100, 1) if total else None


# ----------------------------------------------------------------- 判分
def grade_item(item: dict, record: dict | None) -> dict:
    """一道题（或一篇材料）判分。篇章材料按小题拆成多个计分点。"""
    seq = item.get("seq")
    spec = item.get("acceptable_answers")
    if spec in (None, [], {}):
        raise ValueError(f"seq {seq}: 任务包缺少 acceptable_answers")

    hints = int((record or {}).get("hints_used", 0) or 0)
    response = (record or {}).get("response")
    answered = record is not None and response not in (None, "", {}, [])

    points: list[dict] = []
    if isinstance(spec, dict):  # 篇章材料：{"8": [...], "9": [...]}
        given = response if isinstance(response, dict) else {}
        for no, sub_spec in spec.items():
            student = given.get(str(no), given.get(no))
            points.append(
                {
                    "question_no": no,
                    "response": student,
                    "correct": student not in (None, "") and is_correct(student, sub_spec),
                }
            )
    else:
        points.append(
            {
                "question_no": None,
                "response": response,
                "correct": answered and is_correct(response, spec),
            }
        )

    total = len(points)
    right = sum(1 for p in points if p["correct"])
    if not answered:
        status = "未作答"
    elif right == total:
        status = "提示后正确" if hints else "正确"
    elif right:
        status = "部分正确"
    else:
        status = "错误"

    return {
        "seq": seq,
        "source": item.get("source"),
        "part": item.get("part"),
        "ability": item.get("ability"),
        "knowledge_point": item.get("knowledge_point"),
        "locator": item.get("locator") or item.get("derived_from"),
        "status": status,
        "hints_used": hints,
        "seconds": (record or {}).get("seconds"),
        "points_total": total,
        "points_correct": right,
        # 用了提示就不计入独立掌握：会做和被扶着做不是一回事
        "points_independent": 0 if hints else right,
        "detail": points,
    }


def error_pattern(by_part: dict, graded: list[dict]) -> str | None:
    """与上游 evaluate 同口径的错误模式判定。"""
    # 提示后才做对的同样算没独立拿下，同一考点栽两次就是知识型
    struggled = [g for g in graded if g["status"] in ("错误", "部分正确", "提示后正确")]
    if not struggled:
        return None
    repeated: dict[str, int] = defaultdict(int)
    for g in struggled:
        if g["knowledge_point"]:
            repeated[g["knowledge_point"]] += 1
    if any(count >= 2 for count in repeated.values()):
        return "知识型"
    base, lift = by_part["基础夯实"]["independent"], by_part["能力提升"]["independent"]
    if base is not None and lift is not None:
        if base < lift:
            return "粗心型"
        if base >= 80 and lift < 60:
            return "思路型"
    return "知识型"


def bucket_rates(bucket: list[int]) -> dict:
    independent, assisted, total = bucket
    return {
        "points": total,
        "independent_correct": independent,
        "assisted_correct": assisted,
        "independent": pct(independent, total),
        "with_hints": pct(independent + assisted, total),
    }


# ----------------------------------------------------------------- 报告
REASONS = {"错误": "做错", "部分正确": "部分做对", "提示后正确": "用了提示才做对", "未作答": "没做"}


def tally(graded: list[dict]) -> tuple[dict, dict]:
    """把若干道题的得分点汇进难度层与能力维度两组桶。"""
    by_part: dict[str, list[int]] = {p: [0, 0, 0] for p in PARTS}
    by_ability: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in graded:
        if g["status"] == "未作答":
            continue
        assisted = g["points_correct"] - g["points_independent"]
        buckets = [by_part.get(g["part"])]
        if g["ability"]:
            buckets.append(by_ability[g["ability"]])
        for bucket in buckets:
            if bucket is not None:
                bucket[0] += g["points_independent"]
                bucket[1] += assisted
                bucket[2] += g["points_total"]
    return (
        {p: bucket_rates(b) for p, b in by_part.items()},
        {a: bucket_rates(b) for a, b in sorted(by_ability.items())},
    )


def summarize(graded: list[dict]) -> dict:
    attempted = [g for g in graded if g["status"] != "未作答"]
    total = sum(g["points_total"] for g in attempted)
    independent = sum(g["points_independent"] for g in attempted)
    assisted = sum(g["points_correct"] - g["points_independent"] for g in attempted)
    return {
        "items_planned": len(graded),
        "items_attempted": len(attempted),
        "points_total": total,
        "points_independent": independent,
        "points_assisted": assisted,
        "accuracy_independent": pct(independent, total),
        "accuracy_with_hints": pct(independent + assisted, total),
    }


def build_round(pack: dict, log: dict, index: int) -> dict:
    """一次推题（一份任务包 + 它的作答记录）算一轮，一轮一条记录。"""
    items = pack.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"第 {index} 轮：任务包缺少 items")
    records = {r.get("seq"): r for r in log.get("responses", [])}
    unknown = sorted(str(s) for s in records if s not in {i.get("seq") for i in items})
    if unknown:
        raise ValueError(f"第 {index} 轮：作答记录中的 seq 不在任务包内: {unknown}")

    graded = [grade_item(item, records.get(item.get("seq"))) for item in items]
    part_rates, ability_rates = tally(graded)
    session = pack.get("session") or {}
    tier = pack.get("tier") or {}

    gaps = [f"第 {index} 轮：{g}" for g in log.get("data_gaps", [])]
    if pack.get("bank_available") is False:
        gaps.append(f"第 {index} 轮全部为生成题，报告与话术不含课时与题号定位")

    return {
        "round": index,
        "session_id": pack.get("session_id"),
        "tier_level": tier.get("level"),
        "anchor": session.get("anchor"),
        "units": session.get("units", []),
        "bank_available": pack.get("bank_available"),
        "summary": {
            **summarize(graded),
            "minutes_estimated": session.get("estimated_minutes"),
            "minutes_actual": log.get("minutes_actual"),
            "ended_early": bool(log.get("ended_early", False)),
        },
        "by_part": part_rates,
        "by_ability": ability_rates,
        "items": graded,
        "follow_up": [
            {
                "round": index,
                "seq": g["seq"],
                "reason": REASONS[g["status"]],
                "ability": g["ability"],
                "knowledge_point": g["knowledge_point"],
                "locator": g["locator"],
            }
            for g in graded
            if g["status"] != "正确"
        ],
        "exclude_qids": (pack.get("next_session") or {}).get("exclude_qids", []),
        "data_gaps": gaps,
    }


def build_report(
    pairs: list[tuple[dict, dict]], profile: dict | None, extra_gaps: list[str] | None = None
) -> dict:
    """pairs 是按推题先后排好的 (任务包, 作答记录)，推了几次就有几轮。"""
    if not pairs:
        raise ValueError("至少要有一份任务包与它的作答记录")

    rounds = [build_round(pack, log, i) for i, (pack, log) in enumerate(pairs, 1)]
    graded = [g for r in rounds for g in r["items"]]
    part_rates, ability_rates = tally(graded)
    overall = summarize(graded)

    first_pack = pairs[0][0]
    tier = first_pack.get("tier") or {}
    levels = {r["tier_level"] for r in rounds if r["tier_level"]}

    data_gaps = [g for r in rounds for g in r["data_gaps"]] + list(extra_gaps or [])
    if profile is None:
        data_gaps.append("未提供上游画像，掌握度只给本次值，不给变化量")
    if len(levels) > 1:
        data_gaps.append(f"各轮任务包的层级不一致（{'、'.join(sorted(levels))}），汇总按第 1 轮的层级归口")

    minutes = [r["summary"]["minutes_actual"] for r in rounds if r["summary"]["minutes_actual"]]
    accuracy = overall["accuracy_independent"]
    total_points = overall["points_total"]
    if accuracy is None:
        action, reason = "保持", "本次无有效作答，不构成调层证据"
    elif accuracy >= 85 and total_points >= 6:
        action, reason = "下轮摸底考察升层", f"独立正确率 {accuracy}%，本层题目已不构成挑战"
    elif accuracy < 50:
        action, reason = "下轮摸底考察降层", f"独立正确率 {accuracy}%，本层难度偏高"
    else:
        action, reason = "保持", f"独立正确率 {accuracy}%，本层配比合适"

    mastery_before = (profile or {}).get("mastery", {})
    delta = {}
    for key, rate in part_rates.items():
        after = rate["independent"]
        if after is None:
            continue
        before = mastery_before.get(key)
        delta[key] = {
            "before": before,
            "after": after,
            "delta": round(after - before, 1) if isinstance(before, (int, float)) else None,
        }

    weak = sorted(
        (a for a, r in ability_rates.items() if r["independent"] is not None and r["independent"] < 60),
        key=lambda a: ability_rates[a]["independent"],
    )
    observed_kps: dict[str, int] = defaultdict(int)
    for g in graded:
        if g["status"] in ("错误", "部分正确") and g["knowledge_point"]:
            observed_kps[g["knowledge_point"]] += 1

    excluded: list[str] = []
    for r in rounds:  # 各轮并集，保序去重
        for qid in r["exclude_qids"]:
            if qid not in excluded:
                excluded.append(qid)

    pattern = error_pattern(part_rates, graded)
    return {
        "report_id": f"rep-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "session_id": first_pack.get("session_id"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workbook": first_pack.get("workbook"),
        "bank_available": all(r["bank_available"] is not False for r in rounds),
        "tier": {
            "level": tier.get("level"),
            "name": tier.get("name"),
            "source": "task_pack.tier",
        },
        "rounds_count": len(rounds),
        "rounds": rounds,
        "session_summary": {
            **overall,
            "rounds_count": len(rounds),
            "anchors": [r["anchor"] for r in rounds],
            "units": sorted({u for r in rounds for u in r["units"]}),
            "minutes_actual": sum(minutes) if minutes else None,
            "ended_early": any(r["summary"]["ended_early"] for r in rounds),
        },
        "by_part": part_rates,
        "by_ability": ability_rates,
        "follow_up": [f for r in rounds for f in r["follow_up"]],
        "mastery_delta": delta,
        "error_pattern": pattern,
        "tier_recommendation": {
            "current": tier.get("level"),
            "action": action,
            "reason": reason,
            "confidence": "medium" if total_points >= 8 else "low",
            "decided_by": "build-learner-profile evaluate",
        },
        "profile_patch": {
            "exclude_qids": excluded,
            "mastery_observed": {k: v["independent"] for k, v in part_rates.items()},
            "by_ability_observed": {k: v["independent"] for k, v in ability_rates.items()},
            "focus_abilities_hint": weak,
            "misconceptions_observed": sorted(observed_kps, key=lambda k: observed_kps[k], reverse=True),
            "error_pattern": pattern,
            "apply_note": "调用方负责合并回画像；本 skill 只读不写",
        },
        "data_gaps": data_gaps,
        "out_of_scope": ["层级判定与升降层（属 build-learner-profile）", "跨会话趋势与遗忘曲线复习调度"],
    }


# ----------------------------------------------------------------- 自检
def selfcheck() -> dict:
    pack = {
        "session_id": "bm-test-1",
        "bank_available": True,
        "tier": {"level": "B", "name": "巩固提升"},
        "session": {"anchor": "by + 动名词", "units": ["Unit 1"], "estimated_minutes": 13},
        "items": [
            {"seq": 1, "source": "workbook", "part": "基础夯实", "ability": "汉英转换",
             "knowledge_point": "with 表工具",
             "locator": {"unit": "Unit 1", "period": "第3课时", "question_no": 6},
             "acceptable_answers": ["with his left hand"]},
            {"seq": 2, "source": "workbook", "part": "基础夯实", "ability": "语法辨析",
             "knowledge_point": "by + 动名词", "acceptable_answers": ["B", "B. by watching"]},
            {"seq": 3, "source": "variant", "part": "基础夯实", "ability": "词形变化",
             "knowledge_point": "by + 动名词", "acceptable_answers": ["making"]},
            {"seq": 4, "source": "workbook", "part": "能力提升", "ability": "篇章理解",
             "knowledge_point": "任务型阅读",
             "acceptable_answers": {"8": ["B"], "9": ["A"], "10": ["D"]}},
        ],
        "next_session": {"exclude_qids": ["Unit 1/第3课时/II/6"]},
    }
    log = {
        "minutes_actual": 15,
        "responses": [
            {"seq": 1, "response": "With his left hand.", "hints_used": 0},
            {"seq": 2, "response": "b", "hints_used": 1},
            {"seq": 3, "response": "to make", "hints_used": 0},
            {"seq": 4, "response": {"8": "B", "9": "C", "10": "D"}, "hints_used": 0},
        ],
    }
    profile = {"mastery": {"基础夯实": 66.7, "能力提升": 50.0}}
    report = build_report([(pack, log)], profile)

    assert report["rounds_count"] == 1 and len(report["rounds"]) == 1
    by_seq = {g["seq"]: g for g in report["rounds"][0]["items"]}
    assert by_seq[1]["status"] == "正确", "大小写与句号不应影响判分"
    assert by_seq[2]["status"] == "提示后正确" and by_seq[2]["points_independent"] == 0, "用提示不计独立掌握"
    assert by_seq[3]["status"] == "错误"
    assert by_seq[4]["status"] == "部分正确" and by_seq[4]["points_total"] == 3, "篇章按小题拆计分点"

    summary = report["session_summary"]
    assert summary["points_total"] == 6 and summary["points_independent"] == 3, summary
    assert summary["accuracy_independent"] == 50.0 and summary["accuracy_with_hints"] == 66.7
    assert report["by_part"]["基础夯实"]["independent"] == 33.3
    assert report["by_part"]["能力提升"]["independent"] == 66.7
    assert report["error_pattern"] == "知识型", "同一考点错两次判知识型"
    assert report["mastery_delta"]["基础夯实"]["delta"] == -33.4
    assert report["tier_recommendation"]["action"] == "保持"
    assert [f["seq"] for f in report["follow_up"]] == [2, 3, 4]
    assert report["profile_patch"]["exclude_qids"] == ["Unit 1/第3课时/II/6"]
    assert report["profile_patch"]["focus_abilities_hint"] == ["词形变化", "语法辨析"]

    # 未作答不进正确率分母，题量不足不建议升层
    skipped = build_report([(pack, {"responses": [{"seq": 1, "response": "with his left hand"}]})], None)
    assert skipped["session_summary"]["accuracy_independent"] == 100.0
    assert skipped["session_summary"]["items_attempted"] == 1
    assert skipped["tier_recommendation"]["action"] == "保持"
    assert skipped["mastery_delta"]["基础夯实"]["delta"] is None

    # 推了两次就有两条轮次记录，汇总是两轮合起来算的
    pack2 = {
        "session_id": "bm-test-1",
        "bank_available": True,
        "tier": {"level": "B", "name": "巩固提升"},
        "session": {"anchor": "by + 动名词", "units": ["Unit 2"], "estimated_minutes": 6},
        "items": [
            {"seq": 1, "source": "variant", "part": "基础夯实", "ability": "词形变化",
             "knowledge_point": "by + 动名词", "acceptable_answers": ["making"]},
            {"seq": 2, "source": "variant", "part": "基础夯实", "ability": "词形变化",
             "knowledge_point": "by + 动名词", "acceptable_answers": ["listening"]},
        ],
        "next_session": {"exclude_qids": ["Unit 1/第3课时/II/6", "Unit 2/第1课时/I/3"]},
    }
    log2 = {"minutes_actual": 5, "responses": [
        {"seq": 1, "response": "making", "hints_used": 0},
        {"seq": 2, "response": "listening", "hints_used": 0},
    ]}
    multi = build_report([(pack, log), (pack2, log2)], profile)
    assert multi["rounds_count"] == 2 and len(multi["rounds"]) == 2
    assert [r["round"] for r in multi["rounds"]] == [1, 2]
    assert multi["rounds"][1]["summary"]["accuracy_independent"] == 100.0, "第 2 轮单独算"
    assert multi["rounds"][1]["units"] == ["Unit 2"]
    assert multi["session_summary"]["points_total"] == 8, "两轮的计分点合起来"
    assert multi["session_summary"]["points_independent"] == 5
    assert multi["session_summary"]["accuracy_independent"] == 62.5
    assert multi["session_summary"]["minutes_actual"] == 20, "各轮用时相加"
    assert multi["session_summary"]["units"] == ["Unit 1", "Unit 2"]
    assert {f["round"] for f in multi["follow_up"]} == {1}, "第 2 轮全对，不进错题清单"
    assert multi["profile_patch"]["exclude_qids"] == [
        "Unit 1/第3课时/II/6", "Unit 2/第1课时/I/3"
    ], "各轮并集且保序去重"
    assert multi["by_ability"]["词形变化"]["independent"] == 66.7, "跨轮合并同一维度"

    # 会话文件：推一轮追加一条，做完回填同一条，旧轮次不许被覆盖
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session-test.json"
        append_round(path, pack, None, None)
        append_round(path, None, log, None)
        state = append_round(path, pack2, None, None)
        assert [r["round"] for r in state["rounds"]] == [1, 2]
        assert [r["completed"] for r in state["rounds"]] == [True, False]

        pairs, gaps = pairs_from_session(load_json(path))
        assert len(pairs) == 1 and gaps == ["第 2 轮已推题但没有作答记录，未计入报告"]

        append_round(path, None, log2, None)
        pairs, gaps = pairs_from_session(load_json(path))
        assert len(pairs) == 2 and gaps == []
        from_file = build_report(pairs, profile, gaps)
        assert from_file["rounds_count"] == 2
        assert from_file["session_summary"] == multi["session_summary"], "会话文件与直接传参必须同结果"

        try:
            append_round(path, None, log, None)
        except ValueError as exc:
            assert "没有待回填的轮次" in str(exc)
        else:
            raise AssertionError("回填无处可填时应当报错，而不是悄悄覆盖")
    return {"ok": True, "checks": "全部通过"}


# ----------------------------------------------------------------- CLI
def main() -> None:
    parser = argparse.ArgumentParser(description="缤果学伴学习报告引擎")
    sub = parser.add_subparsers(dest="command", required=True)
    append_cmd = sub.add_parser("append", help="把一轮推题或它的作答记录追加进会话文件")
    append_cmd.add_argument("--session", required=True, help="会话文件 session-<名字>.json，不存在就新建")
    append_cmd.add_argument("--pack", help="本轮任务包 JSON，推题时追加")
    append_cmd.add_argument("--log", help="本轮作答记录 JSON，做完后回填")
    append_cmd.add_argument("--round", type=int, help="回填到指定轮次，缺省填最后一个没记录的轮次")

    report_cmd = sub.add_parser("report", help="产出学习报告与画像补丁")
    report_cmd.add_argument("--session", help="会话文件，里面做完的轮次全部计入")
    report_cmd.add_argument("--pack", action="append",
                            help="任务包 JSON，推了几次就传几次，按先后顺序（不用会话文件时）")
    report_cmd.add_argument("--log", action="append",
                            help="对应那一次推题的作答记录 JSON，与 --pack 一一对应")
    report_cmd.add_argument("--profile", help="上游画像 JSON，给出才有掌握度变化量")
    sub.add_parser("selfcheck", help="内置样例自检")

    args = parser.parse_args()
    try:
        if args.command == "selfcheck":
            emit(selfcheck())
            return
        if args.command == "append":
            emit(
                append_round(
                    Path(args.session),
                    load_json(Path(args.pack)) if args.pack else None,
                    load_json(Path(args.log)) if args.log else None,
                    args.round,
                )
            )
            return
        gaps: list[str] = []
        if args.session:
            if args.pack or args.log:
                raise ValueError("--session 与 --pack/--log 二选一，不要混着传")
            pairs, gaps = pairs_from_session(load_json(Path(args.session)))
        else:
            if not args.pack or not args.log:
                raise ValueError("给一个 --session，或者给成对的 --pack 与 --log")
            if len(args.pack) != len(args.log):
                raise ValueError(f"--pack 传了 {len(args.pack)} 份、--log 传了 {len(args.log)} 份，必须一一对应")
            pairs = [(load_json(Path(p)), load_json(Path(l))) for p, l in zip(args.pack, args.log)]
        emit(build_report(pairs, load_json(Path(args.profile)) if args.profile else None, gaps))
    except Exception as exc:  # 统一错误信封，与上游一致
        emit({"ok": False, "error": str(exc)})
        raise SystemExit(2)


if __name__ == "__main__":
    main()

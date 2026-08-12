#!/usr/bin/env python3
"""作答记录入口自愈。

上游那份作答记录是执行任务的智能体边讲边记的，形状不稳定是常态。
本脚本把它归一化成 report_engine 认识的样子，修不了的明确报错——
**任何情况下都不许改成手工判分**。

    python scripts/intake.py --pack task_pack.json --log raw_log.json --out fixed_log.json

stdout 输出 {"ok": true, "repairs": [...], "issues": [...]}；
issues 非空表示还有人必须处理的问题，退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 作答记录外层：这些键都当成 responses
RESPONSES_KEYS = ("responses", "items", "answers", "records", "log", "responses_list", "结果", "作答")
# 单条记录里：学生答案
RESPONSE_KEYS = ("response", "answer", "student_answer", "reply", "ans", "答案", "作答")
# 单条记录里：提示级数
HINT_KEYS = ("hints_used", "hints", "hint", "hint_level", "hint_used", "提示")
# 单条记录里：题号
SEQ_KEYS = ("seq", "no", "index", "idx", "question_index", "题号")
# 单条记录里：能回溯到任务包的定位
QID_KEYS = ("qid", "id", "locator", "location", "定位")
SECONDS_KEYS = ("seconds", "sec", "duration", "用时")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_key(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def locator_key(loc: Any) -> str | None:
    """把 locator 对象或字符串压成统一的比较键。"""
    if isinstance(loc, str):
        return "/".join(p.strip() for p in loc.split("/") if p.strip())
    if isinstance(loc, dict):
        parts = [loc.get("unit"), loc.get("period"), loc.get("exercise_no"), loc.get("question_no")]
        parts = [str(p).strip() for p in parts if p not in (None, "")]
        return "/".join(parts) if parts else None
    return None


def build_index(pack: dict) -> tuple[dict, dict, list[int], dict]:
    """任务包索引：qid -> seq、locator键 -> seq、全部 seq，以及 seq -> 答案规格。"""
    by_qid: dict[str, int] = {}
    by_loc: dict[str, int] = {}
    seqs: list[int] = []
    spec: dict[int, Any] = {}
    for item in pack.get("items", []):
        seq = item.get("seq")
        if seq is None:
            continue
        seqs.append(seq)
        spec[seq] = item.get("acceptable_answers")
        for k in ("qid", "id"):
            if item.get(k):
                by_qid[str(item[k])] = seq
        lk = locator_key(item.get("locator"))
        if lk:
            by_loc[lk] = seq
            # 末段单独也建一份索引，容忍上游少写一级
            by_loc.setdefault(lk.split("/")[-1], seq)
    return by_qid, by_loc, seqs, spec


def extract_records(raw: Any, repairs: list[str]) -> list[dict]:
    """从任意外层形状里掏出记录列表。"""
    if isinstance(raw, list):
        repairs.append("作答记录最外层是数组，已包成 {\"responses\": [...]}")
        return [r for r in raw if isinstance(r, dict)]
    if not isinstance(raw, dict):
        raise ValueError("作答记录既不是对象也不是数组，没法解析")
    for key in RESPONSES_KEYS:
        val = raw.get(key)
        if isinstance(val, list):
            if key != "responses":
                repairs.append(f"外层键 `{key}` 已改名为 `responses`（脚本只认这一个键，写错会静默判成全未作答）")
            return [r for r in val if isinstance(r, dict)]
    raise ValueError(
        "作答记录里找不到题目数组。期望 {\"responses\": [{...}]}，"
        f"实际顶层键是 {sorted(raw.keys())}"
    )


def normalize(pack: dict, raw_log: Any) -> tuple[dict, list[str], list[str]]:
    repairs: list[str] = []
    issues: list[str] = []

    by_qid, by_loc, pack_seqs, pack_spec = build_index(pack)
    seq_set = set(pack_seqs)
    records = extract_records(raw_log, repairs)
    if not records:
        raise ValueError("作答记录里一道题都没有")

    fixed: list[dict] = []
    used: set[int] = set()
    for pos, rec in enumerate(records):
        out: dict[str, Any] = {}

        # ---- seq：先直取，取不到就靠 qid / locator 回溯，再不行按顺序兜底
        seq = first_key(rec, SEQ_KEYS)
        try:
            seq = int(seq) if seq is not None else None
        except (TypeError, ValueError):
            seq = None

        if seq not in seq_set:
            hint = first_key(rec, QID_KEYS)
            recovered = None
            if hint is not None:
                recovered = by_qid.get(str(hint))
                if recovered is None:
                    lk = locator_key(hint)
                    if lk:
                        recovered = by_loc.get(lk) or by_loc.get(lk.split("/")[-1])
            if recovered is not None:
                repairs.append(f"第 {pos + 1} 条记录靠 `{hint}` 回溯到 seq {recovered}")
                seq = recovered
            elif hint is not None or seq is not None:
                # 写了题号或定位却对不上，说明是真的不匹配（传错文件、题库变了、
                # 上游用了自己的编号）。这种情况下按位置猜会把答案安到别的题上，
                # 而且不会有任何报错——宁可停下来问。
                issues.append(
                    f"第 {pos + 1} 条记录对不上任务包里的任何一道题"
                    f"（题号 {seq!r}、定位 {hint!r}）。"
                    "常见原因：记录与任务包不是同一轮、上游用了自己的编号、题库重抽后定位变了。"
                )
                continue
            elif pos < len(pack_seqs) and pack_seqs[pos] not in used:
                seq = pack_seqs[pos]
                repairs.append(f"第 {pos + 1} 条记录没有任何题号线索，按出现顺序对到 seq {seq}（请人工确认）")
            else:
                issues.append(
                    f"第 {pos + 1} 条记录对不上任务包里的任何一道题"
                    f"（题号 {seq!r}、定位 {first_key(rec, QID_KEYS)!r}）"
                )
                continue

        if seq in used:
            issues.append(f"seq {seq} 在同一份记录里出现了两次，后一条会覆盖前一条，请确认哪条是真的")
        used.add(seq)
        out["seq"] = seq

        # ---- response：原样保留，不替学生改拼写
        resp = first_key(rec, RESPONSE_KEYS)
        if isinstance(resp, list):
            # 篇章材料被写成数组。必须按任务包里的真实小题号还原——
            # 小题号常常是 8/9/10 而不是 1/2/3，按位置编号会全判错且不报错。
            spec = pack_spec.get(seq)
            if isinstance(spec, dict):
                nos = list(spec.keys())
                if len(nos) == len(resp):
                    resp = {str(no): v for no, v in zip(nos, resp)}
                    repairs.append(f"seq {seq} 的作答是数组，已按任务包的小题号 {nos} 还原")
                else:
                    issues.append(
                        f"seq {seq} 的作答有 {len(resp)} 项，任务包有 {len(nos)} 道小题"
                        f"（{nos}），对不上，无法还原——请上游按小题号写成对象"
                    )
                    continue
            else:
                resp = {str(i + 1): v for i, v in enumerate(resp)}
                repairs.append(f"seq {seq} 的作答是数组但任务包不是篇章题，已按位置编号还原（请人工确认）")
        if resp is None:
            repairs.append(f"seq {seq} 没有作答内容，按未作答处理（不进正确率分母）")
        else:
            out["response"] = resp

        # ---- hints_used：缺省 0，但缺省会让掌握度虚高，明确记一条
        hints = first_key(rec, HINT_KEYS)
        if hints is None:
            out["hints_used"] = 0
            repairs.append(f"seq {seq} 没记提示级数，按 0 计——漏记会让独立掌握率偏高")
        else:
            try:
                out["hints_used"] = int(hints)
            except (TypeError, ValueError):
                out["hints_used"] = 1 if hints else 0
                repairs.append(f"seq {seq} 的提示级数 {hints!r} 不是数字，已折算为 {out['hints_used']}")

        secs = first_key(rec, SECONDS_KEYS)
        if secs is not None:
            try:
                out["seconds"] = int(secs)
            except (TypeError, ValueError):
                pass

        fixed.append(out)

    log: dict[str, Any] = {"responses": fixed}
    for key, target in (("minutes_actual", "minutes_actual"), ("用时", "minutes_actual"),
                        ("minutes", "minutes_actual"), ("ended_early", "ended_early")):
        if isinstance(raw_log, dict) and raw_log.get(key) is not None:
            log[target] = raw_log[key]
    if isinstance(raw_log, dict) and isinstance(raw_log.get("data_gaps"), list):
        log["data_gaps"] = raw_log["data_gaps"]

    # ---- 任务包侧的硬伤：缺 acceptable_answers 判不了分，且绝不能猜
    missing = [i.get("seq") for i in pack.get("items", []) if i.get("acceptable_answers") in (None, [], {})]
    if missing:
        issues.append(
            f"任务包里 seq {missing} 缺 acceptable_answers，判不了分。"
            "教辅原题要回源填上，生成题必须自带——不要凭印象补答案。"
        )

    answered = {r["seq"] for r in fixed if "response" in r}
    skipped = sorted(seq_set - answered)
    if skipped:
        repairs.append(f"任务包里 seq {skipped} 没有作答记录，按未作答处理")
    if not answered:
        issues.append("归一化之后一道题都没有作答内容，报告会全是零——先确认作答记录是不是传错了文件")

    return log, repairs, issues


def main() -> None:
    ap = argparse.ArgumentParser(description="把上游作答记录归一化成 report_engine 认识的格式")
    ap.add_argument("--pack", required=True, help="这一轮的任务包 JSON")
    ap.add_argument("--log", required=True, help="这一轮的原始作答记录，形状不限")
    ap.add_argument("--out", required=True, help="归一化后的作答记录写到哪")
    args = ap.parse_args()

    try:
        pack = load(Path(args.pack))
        raw = load(Path(args.log))
        log, repairs, issues = normalize(pack, raw)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    Path(args.out).write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {
        "ok": not issues,
        "out": args.out,
        "items": len(log["responses"]),
        "repairs": repairs,
        "issues": issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

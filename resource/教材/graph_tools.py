#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""薄图谱工具：DAG 校验 + 根因回溯。供用户画像建模模块调用。

  python graph_tools.py validate
  python graph_tools.py probe --chain passive_voice
  python graph_tools.py trace --wrong u07.grammar.passive_modal u06.grammar.passive_past \
                              --right u05.grammar.passive_present
  python graph_tools.py page --kp u06.grammar.passive_past
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
GRAPH_PATH = HERE / "knowledge_graph.json"


def load():
    g = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    g["_by_id"] = {n["kp_id"]: n for n in g["nodes"]}
    return g


def emit(payload):
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# ---------------------------------------------------------------- validate
def validate(g):
    errs, warns = [], []
    by = g["_by_id"]

    for n in g["nodes"]:
        for r in n["requires"]:
            if r not in by:
                errs.append(f"{n['kp_id']} 的前置 {r} 不存在")
        if n["textbook_page"] != 8 * n["unit"] - 4:
            errs.append(f"{n['kp_id']} 页码 {n['textbook_page']} 不符合 8n-4")
        if n["pdf_page"] != n["textbook_page"] + 15:
            errs.append(f"{n['kp_id']} PDF 页码换算错误")
        bank = n.get("exercise_bank")
        if bank and not (PROJECT_ROOT / bank).exists():
            errs.append(f"{n['kp_id']} 题库不存在: {bank}")

    # 环检测
    state = {}
    def visit(k, stack):
        if state.get(k) == "done":
            return
        if state.get(k) == "visiting":
            errs.append("存在环: " + " -> ".join(stack + [k]))
            return
        state[k] = "visiting"
        for r in by[k]["requires"]:
            if r in by:
                visit(r, stack + [k])
        state[k] = "done"
    for n in g["nodes"]:
        visit(n["kp_id"], [])

    for c in g["chains"]:
        ms = set(c["members"])
        if c["terminal"] not in ms:
            errs.append(f"链 {c['chain_id']}: 终点不在成员中")
        for m in ms:
            if m == c["terminal"]:
                continue
            if not (set(by[m]["blocks"]) & ms):
                errs.append(f"链 {c['chain_id']}: {m} 与链内其余节点无边相连")
        tiers = [by[k]["tier"] for k in c["probe_order"]]
        if tiers != sorted(tiers, reverse=True):
            errs.append(f"链 {c['chain_id']}: probe_order 未按层级降序")

    covered = [n["kp_id"] for n in g["nodes"] if n.get("exercise_bank")]
    warns.append(f"仅 {len(covered)}/{len(g['nodes'])} 个节点有习题库，其余在画像中应标 untouched")

    return {"ok": not errs, "errors": errs, "warnings": warns,
            "nodes": len(g["nodes"]),
            "edges": sum(len(n["requires"]) for n in g["nodes"])}


# ------------------------------------------------------------------- probe
def probe(g, chain_id):
    """返回该链的回溯探测顺序：先测链尾高位节点，错了才往前。"""
    c = next((x for x in g["chains"] if x["chain_id"] == chain_id), None)
    if not c:
        raise SystemExit(f"未知链: {chain_id}")
    by = g["_by_id"]
    return {"chain_id": c["chain_id"], "label": c["label"],
            "probe_order": [{"step": i + 1, "kp_id": k, "label": by[k]["label"],
                             "unit": by[k]["unit"],
                             "textbook_page": by[k]["textbook_page"],
                             "pdf_page": by[k]["pdf_page"],
                             "has_bank": bool(by[k].get("exercise_bank")),
                             "on_correct": "该节点及其全部前置判定为 mastered，本链结束",
                             "on_wrong": "继续下一步，向前追溯"}
                            for i, k in enumerate(c["probe_order"])]}


# ------------------------------------------------------------------- trace
def trace(g, wrong, right):
    """根据作答结果定位根因，并给出按依赖顺序排好的补救路径。"""
    by = g["_by_id"]
    wrong, right = set(wrong), set(right)
    for k in wrong | right:
        if k not in by:
            raise SystemExit(f"未知 kp_id: {k}")

    def deps(k, acc=None):
        acc = acc if acc is not None else set()
        for r in by[k]["requires"]:
            if r not in acc:
                acc.add(r)
                deps(r, acc)
        return acc

    # 根因 = 答错且其所有前置都未被判错的节点
    roots = sorted(k for k in wrong if not (deps(k) & wrong))
    blocked = sorted({b for r in roots for b in by[r]["blocks"]} - set(roots))

    state = {}
    for k in right:
        state[k] = "mastered"
        for d in deps(k):
            state.setdefault(d, "mastered")   # 高位对 => 前置默认通过
    for k in wrong:
        state[k] = "severe_weak" if k in roots else "weak"

    path = []
    for k in sorted(roots + blocked, key=lambda x: (by[x]["tier"], by[x]["unit"])):
        path.append({"kp_id": k, "label": by[k]["label"], "unit": by[k]["unit"],
                     "reason": "root_cause" if k in roots else "blocked_by_root",
                     "textbook_page": by[k]["textbook_page"],
                     "pdf_page": by[k]["pdf_page"],
                     "has_bank": bool(by[k].get("exercise_bank"))})

    return {"root_causes": roots, "blocked_by_root": blocked,
            "knowledge_state": [{"kp_id": k, "status": v} for k, v in sorted(state.items())],
            "remediation_path": path,
            "note": "高位节点答对时，其前置按图默认 mastered，无需逐一测试。"}


# -------------------------------------------------------------------- page
def page(g, kp):
    n = g["_by_id"].get(kp)
    if not n:
        raise SystemExit(f"未知 kp_id: {kp}")
    return {"kp_id": kp, "label": n["label"], "unit": n["unit"],
            "unit_pages": n["unit_pages"],
            "grammar_focus": {"textbook_page": n["textbook_page"], "pdf_page": n["pdf_page"]},
            "self_check": {"textbook_page": 8 * n["unit"], "pdf_page": 8 * n["unit"] + 15}}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    p = sub.add_parser("probe"); p.add_argument("--chain", required=True)
    t = sub.add_parser("trace")
    t.add_argument("--wrong", nargs="*", default=[])
    t.add_argument("--right", nargs="*", default=[])
    g_ = sub.add_parser("page"); g_.add_argument("--kp", required=True)
    a = ap.parse_args()

    g = load()
    if a.cmd == "validate":
        r = validate(g); emit(r); sys.exit(0 if r["ok"] else 1)
    elif a.cmd == "probe":
        emit(probe(g, a.chain))
    elif a.cmd == "trace":
        emit(trace(g, a.wrong, a.right))
    elif a.cmd == "page":
        emit(page(g, a.kp))


if __name__ == "__main__":
    main()

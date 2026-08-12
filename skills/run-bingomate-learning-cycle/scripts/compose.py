#!/usr/bin/env python3
"""把 report_engine 的报告 JSON 渲染成文字版报告。

    python scripts/compose.py --report report.json --profile profile.json --out-dir out/

产出三份纯文本（微信里 markdown 不渲染，一律不用 # 和 | 表格）：
  student.txt  给孩子   —— 按层风格，不出现层级，不跨会话
  parent.txt   给家长   —— 只讲事实与下一步，不评价孩子，不出现层级
  teacher.txt  给老师   —— 可以出现层级、置信度、样本量与补丁摘要

数字一律取自报告 JSON，本脚本不重算、不四舍五入美化。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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

# 方案 3.1 的五档，比裸百分比稳：六个计分点撑不住百分比的精度感
BANDS = ((85.0, "已掌握"), (60.0, "基本掌握"), (30.0, "薄弱"), (0.0, "严重薄弱"))
# 少于这个计分点数就不下判断，只报事实
THIN = 3
# 这些是题型不是考点。上游的 knowledge_point 里混着两种，
# 对题型说「这就是一条规则，扎住就对了」是不通的，得换说法。
GENRE_ABILITIES = {"篇章理解"}
GENRE_WORDS = ("阅读", "完形", "写作", "听力", "任务型", "短文")


def is_genre(kp: str | None, ability: str | None = None) -> bool:
    if ability in GENRE_ABILITIES:
        return True
    return bool(kp) and any(w in kp for w in GENRE_WORDS)


def band(pct: float | None, points: int) -> str:
    if points <= 0 or pct is None:
        return "未测到"
    for floor, name in BANDS:
        if pct >= floor:
            return name
    return "严重薄弱"


_CJK = r"\u4e00-\u9fff\u3001\u3002\uff0c\uff1a\uff1b\uff01\uff1f"


def pad(text: str) -> str:
    """中文和拉丁字母/数字挨在一起时补个空格，不然读起来是糊的。

    书名号里的不动——《53同步》是教辅全名，拆成《53 同步》就不是那本书了。
    """
    titles = re.findall(r"《[^》]*》", text)
    for i, t in enumerate(titles):
        text = text.replace(t, f"\x00{i}\x00", 1)
    text = re.sub(rf"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(rf"([A-Za-z0-9%])([\u4e00-\u9fff])", r"\1 \2", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    for i, t in enumerate(titles):
        text = text.replace(f"\x00{i}\x00", t)
    return text


def load(path: str | None) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def locator_text(loc: Any, bank_available: bool, detailed: bool = False) -> str:
    """学生和家长只看到单元；教师明细才可保留内部精确定位。"""
    if not bank_available or not isinstance(loc, dict):
        return ""
    unit = loc.get("unit")
    if not detailed:
        return str(unit) if unit else ""
    parts = [unit, loc.get("period")]
    parts = [str(p) for p in parts if p]
    no = loc.get("question_no")
    ex = loc.get("exercise_no")
    tail = f"{ex} 第 {no} 题" if ex and no else (f"第 {no} 题" if no else "")
    if tail:
        parts.append(tail)
    return " ".join(parts)


def ability_lines(report: dict, profile: dict | None) -> list[dict]:
    """能力维度的当次表现与相对摸底的变化。

    报告 JSON 的 mastery_delta 只到难度层，这里补上维度级——focus_abilities
    是按维度选的，不给维度级变化，'阅读到底进步没有'就没人答得了。
    """
    base = ((profile or {}).get("mastery") or {}).get("by_ability") or {}
    out = []
    for name, cur in (report.get("by_ability") or {}).items():
        points = cur.get("points", 0)
        now = cur.get("independent")
        before = base.get(name)
        out.append({
            "ability": name,
            "points": points,
            "now": now,
            "before": before,
            "delta": (round(now - before, 1) if isinstance(now, (int, float))
                      and isinstance(before, (int, float)) else None),
            "band": band(now, points),
            "thin": points < THIN,
        })
    out.sort(key=lambda r: (r["now"] if r["now"] is not None else 999, -r["points"]))
    return out


def kp_trajectory(report: dict) -> dict[str, dict]:
    """每个考点在各轮的表现轨迹。

    多轮的价值全在这里：第 1 轮错、讲完第 2 轮对，这是「讲完才会的」，
    跟「一上来就会」完全是两件事，对孩子说的话也该不一样。
    """
    traj: dict[str, dict] = {}
    for rnd in report.get("rounds", []):
        for it in rnd.get("items", []):
            kp = it.get("knowledge_point")
            if not kp:
                continue
            t = traj.setdefault(kp, {"kp": kp, "rounds": [], "ability": it.get("ability")})
            t["rounds"].append({
                "round": rnd.get("round"),
                "status": it.get("status"),
                "hints": it.get("hints_used", 0),
                "locator": it.get("locator"),
            })
    for t in traj.values():
        rs = sorted(t["rounds"], key=lambda r: r["round"])
        first, last = rs[0], rs[-1]
        t["recovered"] = (
            len({r["round"] for r in rs}) > 1
            and first["status"] != "正确"
            and last["status"] == "正确"
            and last["hints"] == 0
        )
        t["all_clean"] = all(r["status"] == "正确" and r["hints"] == 0 for r in rs)
        t["still_open"] = last["status"] != "正确" or last["hints"] > 0
        t["locator"] = first.get("locator")
        t["wrong_count"] = sum(1 for r in rs if r["status"] != "正确")
    return traj


def pick_win(report: dict, traj: dict[str, dict]) -> dict | None:
    """挑一条拿下的。讲完才会的优先——那是今天真正发生的事。"""
    recovered = [t for t in traj.values() if t["recovered"]]
    if recovered:
        return {**max(recovered, key=lambda t: t["wrong_count"]), "kind": "recovered"}
    clean = [t for t in traj.values() if t["all_clean"]]
    if clean:
        return {**clean[0], "kind": "clean"}
    return None


def pick_gap(report: dict, traj: dict[str, dict]) -> dict | None:
    """挑最要紧的一条还欠着的。

    已经在后续轮次里独立做对的考点不算欠着——否则会出现
    「拿下的是 X」和「还欠着 X」同时出现，自相矛盾。
    """
    fu = [f for f in (report.get("follow_up") or [])
          if not (traj.get(f.get("knowledge_point"), {}).get("recovered"))]
    if not fu:
        return None
    counts: dict[str, int] = {}
    for f in fu:
        kp = f.get("knowledge_point")
        if kp:
            counts[kp] = counts.get(kp, 0) + 1
    fu = sorted(fu, key=lambda f: (-counts.get(f.get("knowledge_point"), 0),
                                   0 if f.get("reason") == "做错" else 1))
    top = dict(fu[0])
    top["repeat"] = counts.get(top.get("knowledge_point"), 0)
    return top


CN_NUM = "零一二三四五六七八九十"


def cn(n: int | None, measure: bool = False) -> str:
    """小数字写成中文，口语里没人说「做了 6 道题」。

    measure=True 时用于量词前：中文说「两道」「两轮」，不说「二道」。
    """
    if not isinstance(n, int) or n < 0:
        return str(n)
    if measure and n == 2:
        return "两"
    if n <= 10:
        return CN_NUM[n]
    if n < 20:
        return "十" + CN_NUM[n - 10]
    if n < 100 and n % 10 == 0:
        return CN_NUM[n // 10] + "十"
    if n < 100:
        return CN_NUM[n // 10] + "十" + CN_NUM[n % 10]
    return str(n)


def item_tally(report: dict) -> tuple[int, int]:
    """按「题」数，不按得分点——跟孩子说话时得分点是听不懂的内部单位。"""
    done = missed = 0
    for rnd in report.get("rounds", []):
        for it in rnd.get("items", []):
            if it.get("status") == "未作答":
                continue
            done += 1
            if it.get("status") not in ("正确", "提示后正确"):
                missed += 1
    return done, missed


def student_text(report: dict, profile: dict | None, name: str | None) -> str:
    tier = (report.get("tier") or {}).get("level") or "B"
    bank = report.get("bank_available") is not False
    who = name or "同学"
    L: list[str] = []

    done, missed = item_tally(report)
    if missed == 0:
        L.append(f"{who}，今天{cn(done, True)}道题全对。")
    elif missed == 1:
        L.append(f"{who}，今天{cn(done, True)}道题，就一道没拿下。")
    else:
        L.append(f"{who}，今天{cn(done, True)}道题，有{cn(missed, True)}道没拿下。")

    traj = kp_trajectory(report)
    win = pick_win(report, traj)
    if win:
        kp = win["kp"]
        if win["kind"] == "recovered":
            # 今天真正发生的事：一开始不会，讲完自己做对了
            n = win["wrong_count"]
            where = "开头那两道" if n > 1 else "开头那道"
            L.append("")
            if tier == "A":
                L.append(f"{kp}你自己调过来了。{where}没对，后面同样的点你没再问我。"
                         f"自己纠回来的比一上来就会更算数。")
            elif tier == "C":
                L.append(f"最想跟你说的是{kp}。{where}你都栽在这儿，咱们讲完之后，"
                         f"后面同样的点你全做对了，一次都没问我。"
                         f"所以不是你学不会，是这条以前没记牢，现在记牢了。")
            else:
                L.append(f"{kp}这条今天算过了。{where}没对，讲完之后同样的点你自己做对了。")
        else:
            L.append("")
            if tier == "A":
                L.append(f"{kp}你是真会了，一遍就过，没什么好讲的。")
            elif tier == "C":
                L.append(f"先说好的，{kp}你今天做对了，自己做出来的，没让我提醒。这个算是站住了。")
            else:
                L.append(f"稳的是{kp}，你自己做对的。")

    gap = pick_gap(report, traj)
    if gap:
        kp = gap.get("knowledge_point") or gap.get("ability") or "有一个点"
        loc = locator_text(gap.get("locator"), bank)
        rep = gap.get("repeat", 0)
        again = f"同一条今天栽了{cn(rep, True)}次。" if rep > 1 else ""
        genre = is_genre(kp, gap.get("ability"))
        mark = f"书上{loc}那道，翻回去标一下。" if loc else ""
        L.append("")
        if tier == "A":
            L.append(f"还欠着{kp}。{again}这道我不给你答案——你回去重新想一遍，"
                     f"想出来告诉我你卡在哪一步。{mark}")
        elif tier == "C":
            if genre:
                L.append(f"{kp}还差点意思。{again}这种题不用背规则，就是得多读两遍，"
                         f"把答案在哪句话找准。明天我陪你读一篇，一句一句找。{mark}")
            else:
                L.append(f"{kp}还差点意思。{again}别急，就一条规则的事，记牢了这道立刻就对。"
                         f"明天咱们拆开来慢慢过。{mark}")
        else:
            if genre:
                L.append(f"还欠着{kp}。{again}明天我先教你怎么找信息，你再自己读一篇试试。{mark}")
            else:
                L.append(f"还欠着{kp}。{again}明天我先给你个思路，你再自己试一遍。{mark}")
        L.append("")
        L.append("明天就先弄这个，弄完咱们再往下走。")
    elif report.get("follow_up"):
        L.append("")
        L.append("今天错的那些后面你都自己纠回来了，没留下要补的。明天往前走一步，上点新的。")
    else:
        L.append("")
        L.append("没留下要补的。明天往前走一步，上点新的。")

    return "\n".join(L).strip() + "\n"


def parent_text(report: dict, profile: dict | None, name: str | None) -> str:
    """详略按层递增，但层级本身一个字都不出现。只写事实与下一步，不评价孩子。"""
    tier = (report.get("tier") or {}).get("level") or "B"
    s = report.get("session_summary") or {}
    bank = report.get("bank_available") is not False
    who = name or "孩子"
    L: list[str] = []

    traj = kp_trajectory(report)
    units = "、".join(s.get("units") or []) or "本单元"
    attempted = s.get("items_attempted") or 0
    pts = s.get("points_total") or 0
    ind = s.get("points_independent") or 0
    per_round = [(r.get("summary") or {}).get("minutes_actual") for r in report.get("rounds", [])]
    known = [m for m in per_round if isinstance(m, (int, float))]
    mins = sum(known) if len(known) == len(per_round) and known else None
    done, missed = item_tally(report)
    head = f"今日《53同步》{units}，做题 {done} 道"
    if missed == 0:
        head += "，全部做对"
    elif missed == 1:
        head += "，一道没做对"
    else:
        head += f"，{missed} 道没做对"
    head += f"，用时 {mins} 分钟。" if mins else "。"
    L.append(head)
    if s.get("ended_early"):
        L.append("今天没做完全部题目，提前收的。")

    # 掌握度用五档，不用百分比——样本小的时候百分比会误导
    rows = [r for r in ability_lines(report, profile) if not r["thin"]]
    if rows:
        worst = rows[0]
        L.append(f"{worst['ability']}目前是「{worst['band']}」。")
    moved = [r for r in ability_lines(report, profile)
             if r["delta"] is not None and abs(r["delta"]) >= 20 and not r["thin"]]
    if moved:
        m = moved[0]
        d = "有提升" if m["delta"] > 0 else "有回落"
        L.append(f"{m['ability']}较上次记录{d}。")

    rec = [t for t in traj.values() if t["recovered"]]
    if rec:
        L.append(f"{rec[0]['kp']}今天一开始没做对，讲解之后他自己做对了，没有再用提示。")

    gap = pick_gap(report, traj)
    if gap and tier in ("B", "C"):
        kp = gap.get("knowledge_point") or gap.get("ability")
        loc = locator_text(gap.get("locator"), bank)
        L.append(f"还没拿下的是{kp}" + (f"（{loc}）" if loc else "") + "，明天会先补这一条。")

    if tier == "C" and gap:
        kp = gap.get("knowledge_point") or gap.get("ability")
        if is_genre(kp, gap.get("ability")):
            L.append(f"可以做的一件事：让他把今天那篇短文的大意讲给你听一遍，讲得清楚就是真读懂了。")
        else:
            L.append(f"可以做的一件事：让他把「{kp}」这条规则讲给你听一遍，讲得出来就是真会了。")

    return "\n".join(L).strip() + "\n"


TIER_ROUTE = {"A": "拓展挑战", "B": "巩固提升", "C": "稳固基础"}
CONF_CN = {"high": "数据充足", "medium": "数据中等", "low": "数据偏少"}
ACTION_CN = {"保持": "维持当前路线", "上调": "可以考虑加难度", "下调": "建议放缓",
             "hold": "维持当前路线", "up": "可以考虑加难度", "down": "建议放缓"}


def ordinal(n: Any) -> str:
    return f"第{cn(n)}轮" if isinstance(n, int) else "某一轮"


def teacher_text(report: dict, profile: dict | None, name: str | None = None) -> str:
    """给老师和产品看的明细。可以有教学术语，但不出现字段名、编号符号与代码字面量。"""
    tier = report.get("tier") or {}
    s = report.get("session_summary") or {}
    who = name or "该学生"
    L: list[str] = []

    L.append("缤果学伴 · 教师端明细")
    L.append("")
    route = TIER_ROUTE.get(tier.get("level")) or tier.get("name") or "未指定"
    L.append(f"学生：{who}")
    L.append(f"教辅范围：《53同步》{'、'.join(s.get('units') or []) or '本单元'}")
    L.append(f"学习路线：{route}（摸底环节判定，本次不重新判定）")

    done, missed = item_tally(report)
    L.append(f"本次共{cn(report.get('rounds_count') or 0, True)}轮，{done} 道题"
             f"（阅读按小题拆开算，合计 {s.get('points_total')} 小题）。")
    L.append(f"没用提示自己做对 {s.get('points_independent')} 小题，占 {s.get('accuracy_independent')}%；"
             f"算上提示后做对的，共 {s.get('accuracy_with_hints')}%。")
    if report.get("bank_available") is False:
        L.append("本次全部为生成题，报告与话术均不提课时与题号。")
    L.append("")

    L.append("【分轮】")
    for r in report.get("rounds", []):
        rs = r.get("summary") or {}
        actual = rs.get("minutes_actual")
        est = rs.get("minutes_estimated")
        t = f"用时 {actual} 分钟" if isinstance(actual, (int, float)) else "用时未记录"
        if isinstance(est, (int, float)):
            t += f"（预计 {est} 分钟）"
        L.append(f"　{ordinal(r.get('round'))}　本轮重点：{r.get('anchor')}　"
                 f"{rs.get('items_attempted')} 道题　自己做对 {rs.get('accuracy_independent')}%　{t}")
    L.append("")

    L.append("【掌握情况】百分比为没用提示自己做对的比例；本次考得太少的不作判断")
    for r in ability_lines(report, profile):
        line = f"　{r['ability']}　{r['now']}%　{r['band']}"
        if r["delta"] is not None:
            line += f"　较前次记录 {r['delta']:+}"
        line += f"（本次只考了 {r['points']} 小题"
        line += "，不足以判断，不作为调整路线的依据）" if r["thin"] else "）"
        L.append(line)
    L.append("")

    fu = report.get("follow_up") or []
    if fu:
        L.append("【需要跟进】")
        for f in fu:
            loc = locator_text(
                f.get("locator"),
                report.get("bank_available") is not False,
                detailed=True,
            )
            L.append(f"　{ordinal(f.get('round'))}第 {f.get('seq')} 题　{f.get('reason')}　"
                     f"{f.get('ability')} / {f.get('knowledge_point')}"
                     + (f"　{loc}" if loc else "　无教辅出处"))
        L.append("")

    tr = report.get("tier_recommendation") or {}
    act = ACTION_CN.get(tr.get("action"), tr.get("action") or "维持当前路线")
    L.append("【路线建议】")
    reason = (tr.get("reason") or "").replace("本层", "当前路线").replace("层级", "路线")
    reason = reason.replace("独立正确率", "自己做对的比例")
    L.append(f"　{act}。理由：{reason}")
    L.append(f"　本次{CONF_CN.get(tr.get('confidence'), '数据量有限')}，仅供参考；"
             f"路线的最终判定仍由摸底环节负责，本环节不调整。")
    L.append(f"　本次错误类型：{report.get('error_pattern') or '未判定'}")
    L.append("")

    patch = report.get("profile_patch") or {}
    L.append("【学生档案更新】以下内容已由系统写入")
    current_used = patch.get("current_used_item_ids") or []
    L.append(f"　本次用掉的题：{len(current_used)} 道（下次不再重复出）")
    hint = patch.get("focus_abilities_hint") or []
    if hint:
        L.append(f"　下轮建议重点：{'、'.join(hint)}")
    # 上游会把题型也并进误解列表，题型不是误解，滤掉
    mis = [m for m in (patch.get("misconceptions_observed") or []) if not is_genre(m)]
    if mis:
        L.append(f"　本次出错集中在：{'、'.join(mis)}")

    gaps = report.get("data_gaps") or []
    if gaps:
        L.append("")
        L.append("【数据缺口】")
        for g in gaps:
            L.append(f"　{g}")

    L.append("")
    L.append(f"（本次学习编号 {report.get('session_id')}，留档备查）")
    return "\n".join(L).strip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="把报告 JSON 渲染成文字版报告")
    ap.add_argument("--report", required=True, help="report_engine report 的输出")
    ap.add_argument("--profile", help="上游画像，给了才有维度级的变化量")
    ap.add_argument("--name", help="孩子的名字，画像没带就由调用方传")
    ap.add_argument("--out-dir", default=".", help="三份文字报告写到哪")
    args = ap.parse_args()

    report = load(args.report)
    if report.get("ok") is False:
        print(json.dumps({"ok": False, "visibility": "internal", "error": f"传进来的是错误信封，不是报告：{report.get('error')}"},
                         ensure_ascii=False, indent=2))
        raise SystemExit(2)
    profile = load(args.profile)
    profile_name = ((profile or {}).get("learner") or {}).get("name")
    chosen_name = args.name or profile_name

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "student.txt": student_text(report, profile, chosen_name),
        "parent.txt": parent_text(report, profile, chosen_name),
        "teacher.txt": teacher_text(report, profile, chosen_name),
    }
    for fn, text in files.items():
        (out / fn).write_text(pad(text), encoding="utf-8")

    print(json.dumps({"ok": True, "written": [str(out / f) for f in files]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

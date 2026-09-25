"""模态-B 最终画像:lh_score 分腿(LLM/VLM)× 分能力(Build/Edit/Repair/Consistency)。

0720 §3 最终产物,但用 lh_score(部分信用 = 0.5*stage + 0.3*gate + 0.2*raw - 0.05*reg)
而非 pass¹,因为 flash 在 long-horizon 上 raw_passed≈0,pass¹ 无分辨力。

产物:
  - output_b/analysis_b.json  : 每 cell 一行(slot,leg,cap,lh_score,子分,轨迹)
  - stdout                    : LLM vs VLM 总体 + 分能力 lh_score 表 + 轨迹指纹

用法: python3 analyze_b.py [cells_root]  (默认 output_b/cells)
"""
import sys, json, glob
from pathlib import Path
from collections import defaultdict

BENCH = Path("")
CELLS = Path(sys.argv[1]) if len(sys.argv) > 1 else BENCH / "runs/output_b/cells"
TASKS = BENCH / "data/productwebbench/tasks"

# 能力标签归一到 4 类(Extend/Add/Modify → Edit;None → Unknown)
CAP_MAP = {"Extend": "Edit", "Add": "Edit", "Modify": "Edit", "Build": "Build",
           "Edit": "Edit", "Repair": "Repair", "Consistency": "Consistency"}


def cap_of(slot):
    try:
        d = json.loads((TASKS / f"slot_{slot:03d}" / "stage_plan.json").read_text())
        raw = d.get("capability") or d.get("cap")
        return CAP_MAP.get(raw, "Unknown")
    except Exception:
        return "Unknown"


def tier_of(slot):
    """Build 题的难度层 T1-T4(§2.5/§2.9)。非 Build 题返回 None。
    先读 task.jsonl 的 tier 字段,回落到 stage_plan。"""
    for fn in ("task.jsonl", "stage_plan.json"):
        try:
            p = TASKS / f"slot_{slot:03d}" / fn
            if fn.endswith(".jsonl"):
                d = json.loads(p.read_text().splitlines()[0])
            else:
                d = json.loads(p.read_text())
            t = d.get("tier")
            if t:
                t = str(t).upper()
                return t if t in ("T1", "T2", "T3", "T4") else None
        except Exception:
            continue
    return None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def _result_paths():
    # 固定 3 层结构 slot_NNN/<model>/trial_0_{LLM,VLM}/result_b.json,
    # 用 scandir 避免对巨大 workspace/ 子树做 ** 递归(慢)。
    #
    # 模型白名单(2026-07-29 定稿必须):cells/ 下同时存有历史 flash 跑的 650 个 cell
    # (330 LLM + 320 VLM)。不过滤会把 flash 数据混进 pro/sonnet 定稿表 → 统计污染
    # (预演实测 cells=1438 而本轮只有 ~800,且"空转VLM剔除=92"几乎全是非视觉 flash 的
    # VLM 空转)。ONLY_MODELS 为空则不过滤(向后兼容旧用法)。
    import os
    only = {m for m in (os.environ.get("PWB_ONLY_MODELS", "") or "").split(",") if m}
    for slot_dir in os.scandir(CELLS):
        if not slot_dir.name.startswith("slot_"):
            continue
        for model_dir in os.scandir(slot_dir.path):
            if not model_dir.is_dir():
                continue
            if only and model_dir.name not in only:
                continue
            for trial_dir in os.scandir(model_dir.path):
                if not trial_dir.name.startswith("trial_0_"):
                    continue
                p = os.path.join(trial_dir.path, "result_b.json")
                if os.path.exists(p):
                    yield p


def load_cells():
    rows = []
    for rf in _result_paths():
        try:
            r = json.loads(Path(rf).read_text())
        except Exception:
            continue
        tr = r.get("trace", {})
        leg = "VLM" if r.get("vlm_mode") else "LLM"
        # VLM 门禁(0724 doc §3.2 拦截点③):空转 VLM cell(发图却零编辑)标脏、剔除。
        images = tr.get("images_sent", 0) or 0
        edits = tr.get("edits_applied", 0) or 0
        build_errs = tr.get("build_errors", 0) or 0
        invalid_vlm = bool(r.get("vlm_mode")) and images > 0 and edits == 0 and build_errs == 0
        rows.append({
            "slot": r["slot"], "leg": leg, "cap": cap_of(r["slot"]),
            "tier": tier_of(r["slot"]),
            "invalid_vlm": invalid_vlm,
            "task_passed": int(r.get("task_passed", 0) or 0),
            "lh": r.get("lh_score", 0.0),
            # 难度校验的干净尺(见 §4.5 单调性段):gates_passed 不受 stage 链长度影响,
            # build_errors 反映把项目改崩的频率 → 两者都随真实难度单调,lh 不是。
            "gates_passed": r.get("gates_passed", 0) or 0,
            "gates_total": r.get("gates_total", 0) or 0,
            "build_errors": tr.get("build_errors", 0) or 0,
            "stage": r.get("stage_pass_rate", 0.0), "gate": r.get("gate_pass_rate", 0.0),
            "rework": r.get("rework_pass_rate", 0.0), "raw": r.get("raw_passed", 0),
            "completed": bool(tr.get("completed")),
            "turns": tr.get("total_turns"), "steps": tr.get("steps_to_all_clear"),
            "pushbacks": tr.get("pushbacks"), "reg": tr.get("regression_breaks"),
            "clarify": tr.get("clarify_questions"), "edits": tr.get("edits_applied"),
            "images": tr.get("images_sent"), "tokens": tr.get("total_tokens"),
        })
    return rows


def main():
    all_rows = load_cells()
    (CELLS.parent / "analysis_b.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=1))
    # VLM 门禁③:剔除空转 VLM 脏数据后再统计。
    n_invalid = sum(1 for r in all_rows if r.get("invalid_vlm"))
    rows = [r for r in all_rows if not r.get("invalid_vlm")]
    n_slots = len({r["slot"] for r in rows})
    print(f"cells={len(all_rows)}  有效={len(rows)}  空转VLM剔除={n_invalid}  slots={n_slots}\n")

    caps = ["Build", "Edit", "Repair", "Consistency", "Unknown"]

    # === 大分:整题通过率(headline)分腿 × 分能力 ===
    byp = defaultdict(list)
    for r in rows:
        byp[(r["leg"], r["cap"])].append(r["task_passed"])
    print("=== 【大分】整题通过率(task_passed):腿 × 能力 ===")
    print(f"{'leg':4} " + "".join(f"{c:>13}" for c in caps) + f"{'ALL':>13}")
    for leg in ("LLM", "VLM"):
        cells = "".join(f"{mean(byp[(leg,c)]):>8.3f}({len(byp[(leg,c)]):>2})" for c in caps)
        allleg = [r["task_passed"] for r in rows if r["leg"] == leg]
        print(f"{leg:4} {cells}{mean(allleg):>8.3f}({len(allleg):>2})")
    tot_pass = sum(r["task_passed"] for r in rows)
    print(f"整体整题通过:{tot_pass}/{len(rows)} = {mean([r['task_passed'] for r in rows]):.4f}\n")

    # === 小分:分腿 × 分能力 lh_score 矩阵 ===
    by = defaultdict(list)
    for r in rows:
        by[(r["leg"], r["cap"])].append(r["lh"])
    print("=== 【小分】lh_score 均值:腿 × 能力 ===")
    print(f"{'leg':4} " + "".join(f"{c:>13}" for c in caps) + f"{'ALL':>13}")
    for leg in ("LLM", "VLM"):
        cells = "".join(
            f"{mean(by[(leg,c)]):>8.3f}({len(by[(leg,c)]):>2})" for c in caps)
        allleg = [r["lh"] for r in rows if r["leg"] == leg]
        print(f"{leg:4} {cells}{mean(allleg):>8.3f}({len(allleg):>2})")

    # LLM vs VLM 配对 gap(同 slot 两腿都在)
    paired = defaultdict(dict)
    for r in rows:
        paired[r["slot"]][r["leg"]] = r
    both = [(s, d) for s, d in paired.items() if "LLM" in d and "VLM" in d]
    llm_lh = mean([d["LLM"]["lh"] for _, d in both])
    vlm_lh = mean([d["VLM"]["lh"] for _, d in both])
    print(f"\n=== LLM vs VLM(配对 {len(both)} slot 双腿齐全)===")
    print(f"LLM lh={llm_lh:.4f}  VLM lh={vlm_lh:.4f}  VLM-LLM={vlm_lh-llm_lh:+.4f}")
    vlm_win = sum(1 for _, d in both if d["VLM"]["lh"] > d["LLM"]["lh"] + 1e-9)
    llm_win = sum(1 for _, d in both if d["LLM"]["lh"] > d["VLM"]["lh"] + 1e-9)
    tie = len(both) - vlm_win - llm_win
    print(f"VLM 更优 {vlm_win} / LLM 更优 {llm_win} / 平 {tie}")

    # 轨迹指纹(分腿)
    print("\n=== long-horizon 轨迹指纹 ===")
    for leg in ("LLM", "VLM"):
        lr = [r for r in rows if r["leg"] == leg]
        n = max(len(lr), 1)
        print(f"{leg}: 完成率={mean([1 if r['completed'] else 0 for r in lr]):.3f}  "
              f"avg轮={mean([r['turns'] for r in lr]):.0f}  "
              f"stage通过={mean([r['stage'] for r in lr]):.3f}  "
              f"gate通过={mean([r['gate'] for r in lr]):.3f}  "
              f"rework通过={mean([r['rework'] for r in lr]):.3f}  "
              f"回归破坏/题={mean([r['reg'] for r in lr]):.2f}  "
              f"澄清/题={mean([r['clarify'] for r in lr]):.1f}")

    # 分能力总体(不分腿)
    print("\n=== 分能力总体 lh_score(合并双腿)===")
    for c in caps:
        cr = [r["lh"] for r in rows if r["cap"] == c]
        if cr:
            print(f"{c:12} n={len(cr):>3}  lh={mean(cr):.4f}")

    # === Build 难度层 T1-T4(§4.5 单调性)===
    # 难度递增 T1<T2<T3<T4 → 分数应递减 T1≥T2≥T3≥T4(大分 task_passed 与小分 lh 均如此)。
    build_rows = [r for r in rows if r["cap"] == "Build" and r["tier"]]
    if build_rows:
        tiers = ["T1", "T2", "T3", "T4"]
        print("\n=== 【Build 难度层】T1→T4 分数(应随难度递减) ===")
        print(f"{'tier':5}{'n':>5}{'大分task_pass':>16}{'小分lh':>12}{'gates':>9}{'turns':>9}{'builderr':>10}{'完成率':>10}")
        pass_by, lh_by, gate_by, turn_by = {}, {}, {}, {}
        for t in tiers:
            tr = [r for r in build_rows if r["tier"] == t]
            if not tr:
                continue
            p = mean([r["task_passed"] for r in tr])
            l = mean([r["lh"] for r in tr])
            g = mean([r.get("gates_passed") for r in tr])
            tn = mean([r.get("turns") for r in tr])
            be = mean([r.get("build_errors") for r in tr])
            comp = mean([1 if r["completed"] else 0 for r in tr])
            pass_by[t] = p; lh_by[t] = l; gate_by[t] = g; turn_by[t] = tn
            print(f"{t:5}{len(tr):>5}{p:>16.3f}{l:>12.4f}{g:>9.2f}{tn:>9.1f}{be:>10.1f}{comp:>10.3f}")
        # 单调性判定(仅在相邻层都有数据时判定)
        def _monotone(dd, desc=True):
            seq = [dd[t] for t in tiers if t in dd]
            if desc:
                return all(seq[i] >= seq[i + 1] - 1e-9 for i in range(len(seq) - 1)), seq
            return all(seq[i] <= seq[i + 1] + 1e-9 for i in range(len(seq) - 1)), seq
        ok_p, sp = _monotone(pass_by)
        ok_l, sl = _monotone(lh_by)
        ok_g, sg = _monotone(gate_by)              # 难度↑ → 过的门↓
        ok_t, st_ = _monotone(turn_by, desc=False)  # 难度↑ → 花的轮数↑
        print(f"\n单调难度检查(T1→T4):")
        print(f"  【干净尺】gates_passed 递减: {'✅ 单调' if ok_g else '⚠️ 非单调'}  序列={[round(x,2) for x in sg]}")
        print(f"  【干净尺】total_turns 递增:  {'✅ 单调' if ok_t else '⚠️ 非单调'}  序列={[round(x,1) for x in st_]}")
        print(f"  大分 task_pass 递减: {'✅ 单调' if ok_p else '⚠️ 非单调'}  序列={[round(x,3) for x in sp]}")
        print(f"  小分 lh 递减:        {'✅ 单调' if ok_l else '⚠️ 非单调'}  序列={[round(x,4) for x in sl]}")
        # 相邻层做 Welch-style |diff|/se,把噪声级波动与真实反超区分开。
        # 定稿实测(799 cell): gates T3→T4 +0.44 而 se=0.96 → |t|=0.45,是噪声不是反超;
        # turns 则均值与中位数双双严格单调(median 74→97→122→170),是最稳的难度尺。
        import math as _m
        gvals = {t: [r.get("gates_passed") or 0 for r in build_rows if r["tier"] == t] for t in tiers}
        print("  相邻层 gates 差异显著性(|diff|/se,<2 视为噪声):")
        for a, b_ in zip(tiers, tiers[1:]):
            xa, xb = gvals.get(a) or [], gvals.get(b_) or []
            if len(xa) < 2 or len(xb) < 2:
                continue
            ma, mb = sum(xa)/len(xa), sum(xb)/len(xb)
            va = sum((x-ma)**2 for x in xa)/(len(xa)-1)
            vb = sum((x-mb)**2 for x in xb)/(len(xb)-1)
            se = _m.sqrt(va/len(xa) + vb/len(xb)) or 1e-9
            d = mb - ma
            print(f"    {a}→{b_}: diff={d:+.2f} se={se:.2f} |t|={abs(d)/se:.2f}"
                  f" {'(噪声)' if abs(d)/se < 2 else '(显著)'}")
        print("  ⚠️ 难度单调性以 total_turns 为首要准绳,gates 次之,lh 不作难度校验用。")
        print("     lh 含 0.5·stage_pass_rate,而 T1 题 stage 链短 → 一个 stage 未过就掉一大截;")
        print("     T2/T3 stage 多,过掉浅 stage 即垫高分母 → lh 混入了'stage 数量'这一与难度无关的量。")
        print("     ∴ lh 适合做同层内部的部分信用排序,不适合跨难度层做难度校验。")

    print(f"\n写入 {CELLS.parent/'analysis_b.json'}")


if __name__ == "__main__":
    main()

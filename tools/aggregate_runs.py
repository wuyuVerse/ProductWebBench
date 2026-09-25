"""Sweep aggregator: lh_score by leg (LLM/VLM) and by capability
(Build/Edit/Repair/Consistency).

It reports lh_score -- the partial-credit score,
0.5*stage + 0.3*gate + 0.2*raw - 0.05*reg -- rather than the strict whole-task
pass, because raw_passed is near zero for the weaker models on long-horizon
tasks and the strict rate cannot separate them.

Writes:
  - output_b/analysis_b.json  : one row per cell (slot, leg, capability,
                                lh_score, its components, trajectory counters)
  - stdout                    : LLM vs VLM overall, the per-capability lh_score
                                table, and the trajectory fingerprint

Usage: python3 aggregate_runs.py [cells_root]   (default: output_b/cells)
"""
import sys, json, glob
from pathlib import Path
from collections import defaultdict

BENCH = Path("")
CELLS = Path(sys.argv[1]) if len(sys.argv) > 1 else BENCH / "runs/output_b/cells"
TASKS = BENCH / "data/productwebbench/tasks"

# Normalize capability labels to four classes (Extend/Add/Modify -> Edit;
# a task with no label -> Unlabelled)
CAP_MAP = {"Extend": "Edit", "Add": "Edit", "Modify": "Edit", "Build": "Build",
           "Edit": "Edit", "Repair": "Repair", "Consistency": "Consistency"}


def cap_of(slot):
    try:
        d = json.loads((TASKS / f"slot_{slot:03d}" / "stage_plan.json").read_text())
        raw = d.get("capability") or d.get("cap")
        return CAP_MAP.get(raw, "Unlabelled")
    except Exception:
        return "Unlabelled"


def tier_of(slot):
    """Difficulty tier T1-T4 of a Build task; None for a Change task.

    Prefers the tier field of task.jsonl and falls back to the stage plan."""
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
    # The layout is fixed at three levels:
    # slot_NNN/<model>/trial_0_{LLM,VLM}/result_b.json. scandir avoids a ** walk
    # over the very large workspace/ subtrees, which is slow.
    #
    # The model whitelist matters for any final table: the cells/ tree can also
    # hold earlier sweeps of other models, and mixing those into a final table
    # is a statistical contamination -- a dry run counted 1438 cells where the
    # batch itself had about 800, and almost every cell dropped as an idle VLM
    # belonged to the earlier, non-visual sweep. An empty whitelist filters
    # nothing, which keeps older invocations working.
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
        # VLM guard: a cell that was sent an image but applied no edit at all is
        # an idle run. Mark it dirty and drop it.
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
            # The clean rulers for difficulty: gates_passed does not depend on how
            # long the stage chain is, and build_errors tracks how often the project
            # is left broken. Both move monotonically with real difficulty; lh does not.
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
    # Drop the idle-VLM cells before any statistic is taken.
    n_invalid = sum(1 for r in all_rows if r.get("invalid_vlm"))
    rows = [r for r in all_rows if not r.get("invalid_vlm")]
    n_slots = len({r["slot"] for r in rows})
    print(f"cells={len(all_rows)}  usable={len(rows)}  idle-VLM dropped={n_invalid}  slots={n_slots}\n")

    caps = ["Build", "Edit", "Repair", "Consistency", "Unlabelled"]

    # === Headline: whole-task pass rate, by leg and by capability ===
    byp = defaultdict(list)
    for r in rows:
        byp[(r["leg"], r["cap"])].append(r["task_passed"])
    print("=== Whole-task pass rate (task_passed): leg x capability ===")
    print(f"{'leg':4} " + "".join(f"{c:>13}" for c in caps) + f"{'ALL':>13}")
    for leg in ("LLM", "VLM"):
        cells = "".join(f"{mean(byp[(leg,c)]):>8.3f}({len(byp[(leg,c)]):>2})" for c in caps)
        allleg = [r["task_passed"] for r in rows if r["leg"] == leg]
        print(f"{leg:4} {cells}{mean(allleg):>8.3f}({len(allleg):>2})")
    tot_pass = sum(r["task_passed"] for r in rows)
    print(f"overall whole-task pass: {tot_pass}/{len(rows)} = {mean([r['task_passed'] for r in rows]):.4f}\n")

    # === Graded: the lh_score matrix, leg x capability ===
    by = defaultdict(list)
    for r in rows:
        by[(r["leg"], r["cap"])].append(r["lh"])
    print("=== Mean lh_score: leg x capability ===")
    print(f"{'leg':4} " + "".join(f"{c:>13}" for c in caps) + f"{'ALL':>13}")
    for leg in ("LLM", "VLM"):
        cells = "".join(
            f"{mean(by[(leg,c)]):>8.3f}({len(by[(leg,c)]):>2})" for c in caps)
        allleg = [r["lh"] for r in rows if r["leg"] == leg]
        print(f"{leg:4} {cells}{mean(allleg):>8.3f}({len(allleg):>2})")

    # LLM vs VLM paired gap, over slots where both legs ran
    paired = defaultdict(dict)
    for r in rows:
        paired[r["slot"]][r["leg"]] = r
    both = [(s, d) for s, d in paired.items() if "LLM" in d and "VLM" in d]
    llm_lh = mean([d["LLM"]["lh"] for _, d in both])
    vlm_lh = mean([d["VLM"]["lh"] for _, d in both])
    print(f"\n=== LLM vs VLM (paired over {len(both)} slots with both legs) ===")
    print(f"LLM lh={llm_lh:.4f}  VLM lh={vlm_lh:.4f}  VLM-LLM={vlm_lh-llm_lh:+.4f}")
    vlm_win = sum(1 for _, d in both if d["VLM"]["lh"] > d["LLM"]["lh"] + 1e-9)
    llm_win = sum(1 for _, d in both if d["LLM"]["lh"] > d["VLM"]["lh"] + 1e-9)
    tie = len(both) - vlm_win - llm_win
    print(f"VLM better {vlm_win} / LLM better {llm_win} / tie {tie}")

    # Trajectory fingerprint, per leg
    print("\n=== long-horizon trajectory fingerprint ===")
    for leg in ("LLM", "VLM"):
        lr = [r for r in rows if r["leg"] == leg]
        n = max(len(lr), 1)
        print(f"{leg}: completed={mean([1 if r['completed'] else 0 for r in lr]):.3f}  "
              f"avg turns={mean([r['turns'] for r in lr]):.0f}  "
              f"stage pass={mean([r['stage'] for r in lr]):.3f}  "
              f"gate pass={mean([r['gate'] for r in lr]):.3f}  "
              f"rework pass={mean([r['rework'] for r in lr]):.3f}  "
              f"regressions/task={mean([r['reg'] for r in lr]):.2f}  "
              f"clarifications/task={mean([r['clarify'] for r in lr]):.1f}")

    # Per capability, both legs pooled
    print("\n=== lh_score per capability (both legs pooled) ===")
    for c in caps:
        cr = [r["lh"] for r in rows if r["cap"] == c]
        if cr:
            print(f"{c:12} n={len(cr):>3}  lh={mean(cr):.4f}")

    # === Build difficulty tiers T1-T4 ===
    # Difficulty rises T1<T2<T3<T4, so scores should fall T1>=T2>=T3>=T4, for both
    # the whole-task rate and the graded score.
    build_rows = [r for r in rows if r["cap"] == "Build" and r["tier"]]
    if build_rows:
        tiers = ["T1", "T2", "T3", "T4"]
        print("\n=== Build difficulty tiers, T1 to T4 (scores should fall) ===")
        print(f"{'tier':5}{'n':>5}{'task_pass':>12}{'lh':>9}{'gates':>9}{'turns':>9}{'builderr':>10}{'completed':>11}")
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
        # Only judge monotonicity where both adjacent tiers have data
        def _monotone(dd, desc=True):
            seq = [dd[t] for t in tiers if t in dd]
            if desc:
                return all(seq[i] >= seq[i + 1] - 1e-9 for i in range(len(seq) - 1)), seq
            return all(seq[i] <= seq[i + 1] + 1e-9 for i in range(len(seq) - 1)), seq
        ok_p, sp = _monotone(pass_by)
        ok_l, sl = _monotone(lh_by)
        ok_g, sg = _monotone(gate_by)              # harder -> fewer gates passed
        ok_t, st_ = _monotone(turn_by, desc=False)  # harder -> more turns spent
        print(f"\nmonotonicity check over T1 to T4:")
        print(f"  clean ruler, gates_passed falls: {'monotone' if ok_g else 'NOT monotone'}  series={[round(x,2) for x in sg]}")
        print(f"  clean ruler, total_turns rises: {'monotone' if ok_t else 'NOT monotone'}  series={[round(x,1) for x in st_]}")
        print(f"  task_pass falls: {'monotone' if ok_p else 'NOT monotone'}  series={[round(x,3) for x in sp]}")
        print(f"  lh falls: {'monotone' if ok_l else 'NOT monotone'}  series={[round(x,4) for x in sl]}")
        # A Welch-style |diff|/se between adjacent tiers separates noise from a real
        # inversion. Measured on 799 cells: gates rose 0.44 from T3 to T4 with se 0.96,
        # so |t| = 0.45 and that is noise, not an inversion. Turns were strictly
        # monotone in both mean and median (median 74, 97, 122, 170), which makes
        # them the steadiest ruler of the three.
        import math as _m
        gvals = {t: [r.get("gates_passed") or 0 for r in build_rows if r["tier"] == t] for t in tiers}
        print("  adjacent-tier gates difference (|diff|/se; below 2 reads as noise):")
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
                  f" {'(noise)' if abs(d)/se < 2 else '(significant)'}")
        print("  Read total_turns first, gates second; lh is not a difficulty check.")
        print("     lh carries 0.5*stage_pass_rate, and a T1 task has a short stage chain,")
        print("     so one missed stage costs a lot; a T2/T3 task has more stages and")
        print("     clearing the shallow ones lifts the denominator. lh therefore mixes in")
        print("     stage count, which is not a property of difficulty.")
        print("     It ranks partial credit within a tier, not difficulty across tiers.")

    print(f"\nwrote {CELLS.parent/'analysis_b.json'}")


if __name__ == "__main__":
    main()

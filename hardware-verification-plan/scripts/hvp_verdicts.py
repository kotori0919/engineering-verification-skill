#!/usr/bin/env python3
"""Compare verdicts before and after a change: what got fixed, what regressed, what was lost.

  hvp_verdicts.py                                  git:HEAD vs the working tree
  hvp_verdicts.py --before git:<rev> --after git:<rev2>
  hvp_verdicts.py --glob 'verification/**/*.json' --expect-fixed results/clearance.json
  hvp_verdicts.py --before dir:<snapshot> --after dir:.   evidence that git does not track (ignored
                                                   results): snapshot it when the issue opens
  hvp_verdicts.py --json                           machine-readable (input to hvp_fixloop.py attempt)

Reads every *.json that carries a verdict (oracle-catalog 29): a top-level "status" and/or
"metrics" {name: {value, limit, target, sense}}, or items under checks/cases/results/gates
with an id. A check is identified by its file path (plus "#<id>" for list items).

Classes (one per check)
  FIXED        FAIL -> any pass status
  IMPROVED     rank went up without starting from FAIL (e.g. PASS_BELOW_TARGET -> PASS)
  REGRESSED    rank went down (PASS -> PASS_BELOW_TARGET counts)
  LOST         had a verdict before, now missing / NOT_RUN / BLOCKED / unknown
  UNCHANGED    same status
  NEW          verdict only after
Flags (added to the class)
  MARGIN_DROP       same status but a metric's margin to its limit shrank by more than --margin-tol
  CRITERIA_CHANGED  a metric's limit, target or sense differs between before and after
  INCONSISTENT      recorded status is better than the status its own metrics imply
  MODEL_CHANGE      now PASS_BY_MODEL_CHANGE (needs independent confirmation, SKILL 7.11)
Rank: FAIL < PASS_BY_MODEL_CHANGE < PASS_BELOW_TARGET < PASS.

VERDICT ACCEPTABLE needs: no REGRESSED, no LOST, no CRITERIA_CHANGED (unless --allow-criteria,
which is for a user-approved requirement change), no INCONSISTENT after, and every
--expect-fixed check at PASS (or PASS_BELOW_TARGET with --accept-below-target).
MARGIN_DROP is reported; with --strict-margin it also blocks.
Exit codes: 0 ACCEPTABLE, 1 NOT_ACCEPTABLE, 3 input error.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def collect(spec, top, pattern):
    recs = {}
    for path, doc in C.json_files(spec, top, pattern).items():
        recs.update(C.verdict_records(doc, path))
    return recs


def fmt_metrics(b, a):
    parts = []
    for k in sorted(set(b.get("metrics", {})) | set(a.get("metrics", {}))):
        mb, ma = b.get("metrics", {}).get(k, {}), a.get("metrics", {}).get(k, {})
        vb, va = mb.get("value") if isinstance(mb, dict) else None, ma.get("value") if isinstance(ma, dict) else None
        ref = ma if isinstance(ma, dict) and ma else mb
        crit = "limit %s" % ref.get("limit") + (", target %s" % ref.get("target") if ref.get("target") is not None else "")
        parts.append("%s %s -> %s (%s)" % (k, vb, va, crit))
    return "; ".join(parts)


def classify(b, a, tol):
    flags = []
    if b is None:
        return "NEW", flags
    sb, sa = C.effective_status(b), (C.effective_status(a) if a else None)
    rb, ra = C.RANK.get(sb), C.RANK.get(sa)
    if a is not None:
        for k in set(b.get("metrics", {})) & set(a.get("metrics", {})):
            mb, ma = b["metrics"][k], a["metrics"][k]
            if not (isinstance(mb, dict) and isinstance(ma, dict)):
                continue
            if any(mb.get(x) != ma.get(x) for x in ("limit", "target", "sense")):
                flags.append("CRITERIA_CHANGED")
                break
        rec, der = a.get("status"), C.derived_status(a.get("metrics"))
        if rec in C.RANK and der in C.RANK and C.RANK[rec] > C.RANK[der]:
            flags.append("INCONSISTENT")
        if sa == "PASS_BY_MODEL_CHANGE" and sb != sa:
            flags.append("MODEL_CHANGE")
    if rb is not None and ra is None:
        return "LOST", flags
    if rb is None and ra is None:
        return "UNCHANGED", flags
    if rb is None:
        return "NEW", flags
    if ra > rb:
        return ("FIXED" if sb == "FAIL" else "IMPROVED"), flags
    if ra < rb:
        return "REGRESSED", flags
    if a is not None and "CRITERIA_CHANGED" not in flags:
        for k in set(b.get("metrics", {})) & set(a.get("metrics", {})):
            m0, m1 = C.margin(b["metrics"][k]), C.margin(a["metrics"][k])
            if m0 is not None and m1 is not None and m0 > 0 and m1 < m0 - tol * abs(m0):
                flags.append("MARGIN_DROP")
                break
    return "UNCHANGED", flags


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", default="git:HEAD")
    ap.add_argument("--after", default="worktree")
    ap.add_argument("--glob", default="")
    ap.add_argument("--margin-tol", type=float, default=0.10, help="allowed fractional margin shrink (default 0.10)")
    ap.add_argument("--expect-fixed", default="", help="comma list of check ids that this change must fix")
    ap.add_argument("--accept-below-target", action="store_true")
    ap.add_argument("--allow-criteria", action="store_true", help="only for a user-approved requirement change")
    ap.add_argument("--strict-margin", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()
    try:
        B, A = collect(a.before, top, a.glob), collect(a.after, top, a.glob)
    except SystemExit as e:
        print("INPUT_ERROR", e)
        return 3

    rows = {}
    for cid in sorted(set(B) | set(A)):
        cls, flags = classify(B.get(cid), A.get(cid), a.margin_tol)
        rows[cid] = {"class": cls, "flags": flags,
                     "before": C.effective_status(B[cid]) if cid in B else None,
                     "after": C.effective_status(A[cid]) if cid in A else None,
                     "layer": (A.get(cid) or B.get(cid) or {}).get("layer"),
                     "metrics": fmt_metrics(B.get(cid, {}), A.get(cid, {}))}

    reasons = []
    count = lambda c: sum(1 for r in rows.values() if r["class"] == c)  # noqa: E731
    flagc = lambda f: sum(1 for r in rows.values() if f in r["flags"])  # noqa: E731
    if count("REGRESSED"):
        reasons.append("%d regressed" % count("REGRESSED"))
    if count("LOST"):
        reasons.append("%d lost" % count("LOST"))
    if flagc("CRITERIA_CHANGED") and not a.allow_criteria:
        reasons.append("%d criteria changed" % flagc("CRITERIA_CHANGED"))
    if flagc("INCONSISTENT"):
        reasons.append("%d inconsistent" % flagc("INCONSISTENT"))
    if a.strict_margin and flagc("MARGIN_DROP"):
        reasons.append("%d margin drop" % flagc("MARGIN_DROP"))
    ok_after = {"PASS"} | ({"PASS_BELOW_TARGET"} if a.accept_below_target else set())
    for want in [x for x in a.expect_fixed.split(",") if x]:
        hit = [c for c in rows if c == want or c.startswith(want + "#")]
        if not hit:
            reasons.append("expected check not found: %s" % want)
        for c in hit:
            if rows[c]["after"] not in ok_after:
                reasons.append("expected fix not reached: %s is %s" % (c, rows[c]["after"]))
    verdict = "NOT_ACCEPTABLE" if reasons else "ACCEPTABLE"

    if a.json:
        print(json.dumps({"before": a.before, "after": a.after, "checks": rows, "verdict": verdict,
                          "reasons": reasons}, indent=1, sort_keys=True))
    else:
        order = ["REGRESSED", "LOST", "FIXED", "IMPROVED", "NEW", "UNCHANGED"]
        for cls in order:
            for cid, r in rows.items():
                if r["class"] != cls or (cls == "UNCHANGED" and not r["flags"]):
                    continue
                print("%-10s %s  %s -> %s%s%s" % (cls, cid, r["before"], r["after"],
                                                   "  [" + ",".join(r["flags"]) + "]" if r["flags"] else "",
                                                   "  " + r["metrics"] if r["metrics"] else ""))
        summ = " ".join("%s=%d" % (c.lower(), count(c)) for c in order)
        summ += " " + " ".join("%s=%d" % (f.lower(), flagc(f)) for f in
                               ("MARGIN_DROP", "CRITERIA_CHANGED", "INCONSISTENT", "MODEL_CHANGE"))
        print("SUMMARY", summ)
        print("VERDICT", verdict + (": " + "; ".join(reasons) if reasons else ""))
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())

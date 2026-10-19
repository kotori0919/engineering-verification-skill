#!/usr/bin/env python3
"""Fix-loop ledger: attempt budget, oscillation and stall detection for one root cause.

  hvp_fixloop.py open    --sid S --issue I --checks <id,...> --cause "<one line>" [--budget 3]
  hvp_fixloop.py attempt --sid S --issue I --change "<what changed>" [--params k=v ...]
                         --verdicts <hvp_verdicts.py --json output file>
  hvp_fixloop.py check   --sid S --issue I          CONTINUE (exit 0) or STOP <reasons> (exit 3)
  hvp_fixloop.py close   --sid S --issue I --result fixed|escalated|abandoned [--note "..."]
  hvp_fixloop.py show    --sid S [--md]             ledger, or a markdown table (templates 7b)

The ledger lives next to the session lock: <git-common-dir>/hvp-sessions/<SID>.fixlog.json
(--ledger PATH overrides). It is a working aid; copy `show --md` into the repo's fix report.
`attempt --verdicts` must compare against the state BEFORE the issue was opened
(hvp_verdicts.py --before git:<start commit>), so every attempt is measured from the same base.

STOP reasons
  BUDGET     attempts used >= budget
  REPEAT     the same parameter values were already tried in an earlier attempt
  COUPLED    two checks trade places: one is fixed while the other regresses, then the reverse.
             Fix them together (joint sweep) or escalate; do not keep alternating
  OSCILLATING a check went up and then down again across attempts
  STALLED    the issue's target checks did not improve over the last two attempts
  CLOSED     the issue is already closed
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def ledger_path(a):
    return a.ledger or os.path.join(C.lock_dir(), "%s.fixlog.json" % a.sid)


def load(p):
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"issues": {}}


def save(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(tmp, p)


def rank(s):
    return C.RANK.get(s, -1)


def evaluate(iss):
    reasons = []
    att = iss["attempts"]
    if iss.get("result"):
        reasons.append("CLOSED (%s)" % iss["result"])
    if len(att) >= iss["budget"]:
        reasons.append("BUDGET %d/%d attempts used" % (len(att), iss["budget"]))
    seen = {}
    for i, x in enumerate(att, 1):
        key = json.dumps(x.get("params") or {}, sort_keys=True)
        if x.get("params") and key in seen:
            reasons.append("REPEAT attempt %d reuses the parameters of attempt %d %s" % (i, seen[key], key))
        seen.setdefault(key, i)
    checks = sorted(set().union(*[set(x["status"]) for x in att]) if att else set())
    # each check's rank sequence: baseline (before the issue) followed by every attempt
    base = att[0].get("before", {}) if att else {}
    full = {c: [rank(base.get(c))] + [rank(x["status"].get(c)) for x in att] for c in checks}
    for c, s in full.items():
        ups = [i for i in range(1, len(s)) if s[i] > s[i - 1]]
        downs = [i for i in range(1, len(s)) if s[i] < s[i - 1]]
        if any(d > u for u in ups for d in downs):
            reasons.append("OSCILLATING %s ranks %s (baseline first)" % (c, s))
    pairs = set()
    for i in range(1, len(att)):
        for c1 in checks:
            for c2 in checks:
                if c1 >= c2:
                    continue
                s1, s2 = full[c1], full[c2]
                a_then = s1[i] > s1[i - 1] and s2[i] < s2[i - 1]
                b_now = s2[i + 1] > s2[i] and s1[i + 1] < s1[i]
                a_then_r = s2[i] > s2[i - 1] and s1[i] < s1[i - 1]
                b_now_r = s1[i + 1] > s1[i] and s2[i + 1] < s2[i]
                if (a_then and b_now) or (a_then_r and b_now_r):
                    pairs.add((c1, c2))
    for c1, c2 in sorted(pairs):
        reasons.append("COUPLED %s <-> %s trade places across attempts" % (c1, c2))
    if len(att) >= 3:
        best = lambda x: min(rank(x["status"].get(c)) for c in iss["checks"])  # noqa: E731
        if best(att[-1]) <= best(att[-3]) and best(att[-2]) <= best(att[-3]):
            reasons.append("STALLED target checks %s not improved over the last two attempts" % ",".join(iss["checks"]))
    return reasons


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["open", "attempt", "check", "close", "show"])
    ap.add_argument("--sid", required=True)
    ap.add_argument("--issue")
    ap.add_argument("--ledger")
    ap.add_argument("--checks", default="")
    ap.add_argument("--cause", default="")
    ap.add_argument("--budget", type=int, default=3)
    ap.add_argument("--change", default="")
    ap.add_argument("--params", nargs="*", default=[])
    ap.add_argument("--verdicts")
    ap.add_argument("--result", choices=["fixed", "escalated", "abandoned"])
    ap.add_argument("--note", default="")
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args()
    p = ledger_path(a)
    d = load(p)
    if a.op != "show" and not a.issue:
        print("--issue required")
        return 3
    iss = d["issues"].get(a.issue)

    if a.op == "open":
        if iss:
            print("issue exists:", a.issue)
            return 1
        d["issues"][a.issue] = {"cause": a.cause, "checks": [c for c in a.checks.split(",") if c],
                                "budget": a.budget, "opened": int(time.time()),
                                "start_commit": C.head(), "attempts": [], "result": None, "note": ""}
        save(p, d)
        print("OPEN %s budget=%d checks=%s start=%s ledger=%s" % (a.issue, a.budget, a.checks, C.head()[:12], p))
        return 0
    if iss is None and a.op != "show":
        print("unknown issue:", a.issue)
        return 3

    if a.op == "attempt":
        if not a.verdicts:
            print("--verdicts required (hvp_verdicts.py --json output)")
            return 3
        v = json.load(open(a.verdicts))
        rows = v.get("checks", {})
        params = dict(kv.split("=", 1) for kv in a.params)
        iss["attempts"].append({
            "change": a.change, "params": params, "time": int(time.time()),
            "verdict": v.get("verdict"), "reasons": v.get("reasons", []),
            "status": {c: r.get("after") for c, r in rows.items()},
            "before": {c: r.get("before") for c, r in rows.items()},
            "classes": {c: "+".join(([r["class"]] if r.get("class") not in ("UNCHANGED", None) else [])
                                    + list(r.get("flags") or []))
                        for c, r in rows.items()
                        if r.get("class") not in ("UNCHANGED", None) or r.get("flags")}})
        save(p, d)
        n = len(iss["attempts"])
        tgt = ", ".join("%s=%s" % (c, iss["attempts"][-1]["status"].get(c)) for c in iss["checks"])
        print("ATTEMPT %d/%d recorded: %s | %s | %s" % (n, iss["budget"], a.change, v.get("verdict"), tgt))
        a.op = "check"

    if a.op == "check":
        reasons = evaluate(iss)
        if reasons:
            print("STOP")
            for r in reasons:
                print("  " + r)
            return 3
        print("CONTINUE %d/%d attempts used" % (len(iss["attempts"]), iss["budget"]))
        return 0

    if a.op == "close":
        iss["result"], iss["note"] = a.result, a.note
        save(p, d)
        print("CLOSED %s %s" % (a.issue, a.result))
        return 0

    # show
    issues = d["issues"] if not a.issue else {a.issue: d["issues"].get(a.issue, {})}
    if not a.md:
        print(json.dumps(issues, indent=1, sort_keys=True))
        return 0
    for name, it in issues.items():
        if not it:
            continue
        print("#### %s — %s (budget %d, result %s)\n" % (name, it["cause"], it["budget"], it.get("result") or "open"))
        print("| # | 변경 | 파라미터 | 목표 검사 상태 | 다른 변화 | 판정 |")
        print("| --- | --- | --- | --- | --- | --- |")
        for i, x in enumerate(it["attempts"], 1):
            tgt = ", ".join("%s %s" % (c.split("/")[-1], x["status"].get(c)) for c in it["checks"])
            other = ", ".join("%s %s" % (c.split("/")[-1], k) for c, k in sorted(x["classes"].items())
                              if c not in it["checks"]) or "-"
            prm = ", ".join("%s=%s" % kv for kv in sorted(x["params"].items())) or "-"
            print("| %d | %s | %s | %s | %s | %s |" % (i, x["change"], prm, tgt, other, x["verdict"]))
        if it.get("note"):
            print("\n" + it["note"])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

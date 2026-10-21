#!/usr/bin/env python3
"""Composite verification: do independent methods agree on the same quantity?

  hvp_consensus.py --q ev:results/sf_closed_form.json#/metrics/safety_factor/value \
                   --q ev:results/sf_fe.json#/metrics/safety_factor/value \
                   --q ev:results/sf_coupon.json#/metrics/safety_factor/value \
                   [--u 0.05,0.08,0.10] [--limit 1.0 --target 1.5 --sense min] [--out consensus.json]

Each --q names one method's value (closed form, FE, a second solver, a test). Its expanded
uncertainty U (about 95 %) is read from the sibling "uncertainty" field of the metric entry, or
given with --u in the same order. limit/target/sense are taken from the first metric entry
unless given.

Agreement per pair uses the normalized error E_n = |x_i - x_j| / sqrt(U_i^2 + U_j^2)
(ISO 13528): |E_n| <= 1 agree, > 1 disagree.
Result
  CONSENSUS   all pairs agree; the verdict uses the conservative bound over all methods
              (min of x - U for sense min, max of x + U for sense max), never the average
  DISAGREE    at least one pair disagrees: status BLOCKED. A disagreement is a finding about a
              model, an input or a test; find which method is wrong before using any of them
Warnings
  NO_UNCERTAINTY   a method has no stated uncertainty (treated as 0, so it agrees with nothing
                   unless equal): state one, from GCI, input spread or gauge study
  NOT_INDEPENDENT  two methods were produced by the same command or share an evidence file:
                   they can share the same mistake, so their agreement proves little
Exit codes: 0 CONSENSUS with a passing conservative status, 1 otherwise, 3 input error.
"""
import argparse
import itertools
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
from hvp_cite import pointer  # noqa: E402


def parse_q(q):
    q = q[3:] if q.startswith("ev:") else q
    path, ptr = q.split("#", 1)
    return path, ptr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--q", action="append", required=True, help="ev:<file>#<pointer>, one per method")
    ap.add_argument("--u", help="expanded uncertainties in --q order (overrides the files)")
    ap.add_argument("--limit", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--sense", choices=["min", "max"])
    ap.add_argument("--out")
    a = ap.parse_args()
    if len(a.q) < 2:
        print("INPUT_ERROR need at least two methods")
        return 3
    top = C.toplevel()
    us = [float(x) for x in a.u.split(",")] if a.u else None
    methods, warns = [], []
    crit = {}
    for i, q in enumerate(a.q):
        try:
            path, ptr = parse_q(q)
            doc = json.load(open(os.path.join(top, path)))
            val = float(pointer(doc, ptr))
            parent = pointer(doc, ptr.rsplit("/", 1)[0]) if ptr.count("/") > 1 else {}
        except Exception as e:  # noqa: BLE001
            print("INPUT_ERROR %s: %s" % (q, e))
            return 3
        parent = parent if isinstance(parent, dict) else {}
        if not crit and "limit" in parent:
            crit = {k: parent.get(k) for k in ("limit", "target", "sense")}
        u = us[i] if us else parent.get("uncertainty")
        if not C._num(u):
            warns.append("NO_UNCERTAINTY %s" % q)
            u = 0.0
        pv = doc.get("provenance") if isinstance(doc.get("provenance"), dict) else {}
        methods.append({"q": q, "file": path, "value": val, "U": abs(float(u)),
                        "method": doc.get("method") or path, "command": pv.get("command")})
    for k in ("limit", "target", "sense"):
        if getattr(a, k) is not None:
            crit[k] = getattr(a, k)
    sense = crit.get("sense") or "min"

    for m1, m2 in itertools.combinations(methods, 2):
        if m1["file"] == m2["file"] or (m1["command"] and m1["command"] == m2["command"]):
            warns.append("NOT_INDEPENDENT %s and %s" % (m1["method"], m2["method"]))

    pairs, agree = [], True
    for m1, m2 in itertools.combinations(methods, 2):
        den = math.hypot(m1["U"], m2["U"])
        diff = abs(m1["value"] - m2["value"])
        en = (diff / den) if den else (0.0 if diff == 0 else float("inf"))
        ok = en <= 1.0
        agree &= ok
        pairs.append((m1["method"], m2["method"], en, ok))

    print("METHODS")
    for m in methods:
        print("  %-40s %.6g ± %.3g   (%s)" % (m["method"][:40], m["value"], m["U"], m["q"]))
    print("PAIRS   E_n = |x_i - x_j| / sqrt(U_i^2 + U_j^2), agree when <= 1")
    for n1, n2, en, ok in pairs:
        print("  %-6s E_n %-8.3g %s vs %s" % ("agree" if ok else "DIFFER", en, n1[:30], n2[:30]))
    for w in warns:
        print("WARNING", w)

    bound = min(m["value"] - m["U"] for m in methods) if sense == "min" else max(m["value"] + m["U"] for m in methods)
    status = None
    if crit.get("limit") is not None:
        status = C.metric_status({"value": bound, "limit": crit["limit"], "target": crit.get("target"), "sense": sense})
    if not agree:
        result, final = "DISAGREE", "BLOCKED"
    else:
        result, final = "CONSENSUS", status or "NO_CRITERIA"
    print("BOUND   conservative %s over all methods = %.6g%s" % (
        "min(x - U)" if sense == "min" else "max(x + U)", bound,
        " vs limit %s, target %s" % (crit.get("limit"), crit.get("target")) if crit.get("limit") is not None else ""))
    print("RESULT  %s -> %s" % (result, final))
    if result == "DISAGREE":
        print("NOTE    do not average or pick the convenient method; find which model, input or test is wrong")
    if a.out:
        json.dump({"status": final if final != "NO_CRITERIA" else None, "layer": "L3",
                   "scope": "agreement of %d independent methods on one quantity" % len(methods),
                   "assumptions": ["stated U are expanded (about 95 %) uncertainties", "methods are independent"],
                   "limitations": ["agreement does not prove a shared assumption is right"],
                   "provenance": {"command": " ".join(sys.argv)},
                   "methods": methods, "pairs": [{"a": x, "b": y, "En": e, "agree": o} for x, y, e, o in pairs],
                   "warnings": warns,
                   "metrics": {"conservative_bound": dict(crit, value=bound)} if crit.get("limit") is not None else {}},
                  open(a.out, "w"), indent=1)
    return 0 if (result == "CONSENSUS" and final in C.PASSING) else 1


if __name__ == "__main__":
    sys.exit(main())

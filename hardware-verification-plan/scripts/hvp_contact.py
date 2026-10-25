#!/usr/bin/env python3
"""Contact map along a motion path, and what a fix did to it.

  hvp_contact.py map  samples.csv --limit 0.2 [--intended intended.json] [--out results/contact_map.json]
  hvp_contact.py diff before.csv after.csv --limit 0.2 [--intended intended.json] [--tol 0.05] [--out ...]

Samples: one row per (pair, input position), from whatever computes distances (OCC
BRepExtrema, coal/FCL, a CAD API probe, the data layer). CSV with columns pair,s,d, or a JSON
list of {"pair", "s", "d"}. s is the input coordinate (handle travel mm, crank angle deg ...),
d the signed minimum distance (negative = penetration depth), in the same unit as --limit.
Column names can be changed with --cols pair,s,d.
--intended: JSON list of pairs that are meant to touch (gear mesh, pin in its groove), with a
reason each: [{"pair": "pin--groove", "reason": "follower contact, catalog 7"}]. They are
reported apart and do not fail the map.

map  per pair: contact intervals (consecutive samples with d < limit along s), for each the
     start, end, worst d and where it occurs; the first contact along s (the "moment" the motion
     first meets something); pairs ranked by worst d; sample spacing.
     SINGLE_SAMPLE marks an interval seen at one sample only: refine around it (catalog 6),
     the true extent and depth are unknown.
     status FAIL if an unintended pair has d < limit, else PASS. Metric min_clearance (sense min).
diff per pair, before -> after:
       RESOLVED   violated before, clear after
       NEW        clear before, violated after
       MOVED      violated in both, the intervals do not overlap along s (contact relocated)
       PERSISTS   violated in both, overlapping, worst d changed by <= --tol
       IMPROVED / WORSENED   overlapping, worst d changed by more than --tol
     WHACK_A_MOLE when one pair is RESOLVED while another is NEW or MOVED: the change moved the
     contact instead of removing its cause (a path or constraint mismatch, not one dimension).
     Read catalog 42 before the next attempt.
Exit codes: 0 no unintended violation (map) / after is clear (diff), 1 otherwise, 3 input error.
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def load(path, cols):
    pc, sc, dc = cols
    if path.endswith(".json"):
        doc = json.load(open(path))
        if isinstance(doc, dict) and "data" in doc and "rows" in doc["data"]:
            names = doc["data"]["columns"]
            doc = [dict(zip(names, r)) for r in doc["data"]["rows"]]
        rows = doc
    else:
        rows = list(csv.DictReader(open(path, newline="")))
    out = {}
    for r in rows:
        try:
            out.setdefault(str(r[pc]), []).append((float(r[sc]), float(r[dc])))
        except (KeyError, TypeError, ValueError) as e:
            raise SystemExit("INPUT_ERROR %s: row %s (%s)" % (path, r, e))
    for k in out:
        out[k].sort()
    return out


def intervals(samples, limit):
    res, cur = [], None
    for s, d in samples:
        if d < limit:
            if cur is None:
                cur = {"s_start": s, "s_end": s, "worst_d": d, "s_worst": s, "samples": 0}
            cur["s_end"] = s
            cur["samples"] += 1
            if d < cur["worst_d"]:
                cur["worst_d"], cur["s_worst"] = d, s
        elif cur is not None:
            res.append(cur)
            cur = None
    if cur is not None:
        res.append(cur)
    return res


def spacing(samples):
    ss = [s for s, _ in samples]
    gaps = [b - a for a, b in zip(ss, ss[1:]) if b > a]
    return max(gaps) if gaps else None


def contact_map(data, limit, intended):
    pairs = {}
    for p, sm in data.items():
        iv = intervals(sm, limit)
        worst = min(sm, key=lambda x: x[1])
        pairs[p] = {"intervals": iv, "min_d": worst[1], "s_at_min": worst[0], "samples": len(sm),
                    "max_spacing": spacing(sm), "intended": p in intended,
                    "single_sample": any(x["samples"] == 1 for x in iv)}
    return pairs


def print_map(pairs, limit, intended):
    viol = {p: v for p, v in pairs.items() if v["intervals"] and not v["intended"]}
    first = min(((iv["s_start"], p) for p, v in viol.items() for iv in v["intervals"]), default=None)
    print("PAIRS    %d   limit %g   unintended in contact %d   intended %d" % (
        len(pairs), limit, len(viol), sum(1 for v in pairs.values() if v["intended"])))
    if first:
        print("FIRST    %s at s=%g (first unintended contact along the input)" % (first[1], first[0]))
    for p, v in sorted(pairs.items(), key=lambda kv: kv[1]["min_d"]):
        tag = "intended (%s)" % intended[p] if v["intended"] else ("CONTACT" if v["intervals"] else "clear")
        print("  %-30s min d %-9.4g at s=%-9.4g %s%s" % (p, v["min_d"], v["s_at_min"], tag,
                                                       "  SINGLE_SAMPLE" if v["single_sample"] and not v["intended"] else ""))
        for iv in v["intervals"] if not v["intended"] else []:
            print("      s %g .. %g   worst %.4g at s=%g   (%d samples, spacing <= %s)" % (
                iv["s_start"], iv["s_end"], iv["worst_d"], iv["s_worst"], iv["samples"], v["max_spacing"]))
    return viol, first


def overlap(a, b):
    return a["s_start"] <= b["s_end"] and b["s_start"] <= a["s_end"]


def classify(b, a, tol):
    bi, ai = b["intervals"] if b else [], a["intervals"] if a else []
    if bi and not ai:
        return "RESOLVED"
    if ai and not bi:
        return "NEW"
    if not ai and not bi:
        return "CLEAR"
    if not any(overlap(x, y) for x in bi for y in ai):
        return "MOVED"
    dw = min(y["worst_d"] for y in ai) - min(x["worst_d"] for x in bi)
    if abs(dw) <= tol:
        return "PERSISTS"
    return "IMPROVED" if dw > 0 else "WORSENED"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["map", "diff"])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--limit", type=float, required=True)
    ap.add_argument("--intended")
    ap.add_argument("--tol", type=float, default=0.0)
    ap.add_argument("--cols", default="pair,s,d")
    ap.add_argument("--out")
    a = ap.parse_args()
    cols = a.cols.split(",")
    intended = {}
    if a.intended:
        for x in json.load(open(a.intended)):
            intended[x["pair"]] = x.get("reason") or "no reason given"
    need = 1 if a.op == "map" else 2
    if len(a.files) != need:
        print("INPUT_ERROR %s takes %d file(s)" % (a.op, need))
        return 3
    if a.op == "map":
        pairs = contact_map(load(a.files[0], cols), a.limit, intended)
        viol, first = print_map(pairs, a.limit, intended)
        unint = [v for v in pairs.values() if not v["intended"]]
        m = min(unint, key=lambda v: v["min_d"]) if unint else None
        status = "FAIL" if viol else "PASS"
        print("STATUS   %s" % status)
        if a.out:
            wp = min(((v["min_d"], p, v["s_at_min"]) for p, v in pairs.items() if not v["intended"]), default=None)
            ev = {"status": status, "layer": "L2", "method": "contact map from sampled signed distances",
                  "scope": "pairs and input range in %s" % a.files[0],
                  "assumptions": ["d is the signed minimum distance per pair at each sample"],
                  "limitations": ["between samples nothing is known unless the spacing meets catalog 6"],
                  "inputs": {os.path.relpath(a.files[0]): C.sha256_file(a.files[0])},
                  "provenance": {"command": " ".join(sys.argv)},
                  "metrics": {"min_clearance": {"value": m["min_d"], "limit": a.limit, "sense": "min"}} if m else {},
                  "data": {"where": {"pair": wp[1], "s": wp[2]} if wp else None,
                           "first_contact": {"pair": first[1], "s": first[0]} if first else None,
                           "pairs": pairs, "intended": intended}}
            json.dump(ev, open(a.out, "w"), indent=1, sort_keys=True)
        return 1 if viol else 0

    before = contact_map(load(a.files[0], cols), a.limit, intended)
    after = contact_map(load(a.files[1], cols), a.limit, intended)
    rows = {}
    for p in sorted(set(before) | set(after)):
        if p in intended:
            continue
        rows[p] = classify(before.get(p), after.get(p), a.tol)
    print("DIFF     %s -> %s   limit %g   tol %g" % (a.files[0], a.files[1], a.limit, a.tol))
    for p, c in sorted(rows.items(), key=lambda kv: kv[1]):
        if c == "CLEAR":
            continue
        b, f = before.get(p), after.get(p)
        desc = lambda v: ("; ".join("s %g..%g worst %.4g" % (i["s_start"], i["s_end"], i["worst_d"])  # noqa: E731
                                    for i in v["intervals"]) or "clear") if v else "absent"
        print("  %-10s %-30s before: %s | after: %s" % (c, p, desc(b), desc(f)))
    counts = {}
    for c in rows.values():
        counts[c] = counts.get(c, 0) + 1
    whack = counts.get("RESOLVED", 0) > 0 and (counts.get("NEW", 0) + counts.get("MOVED", 0)) > 0
    remaining = [p for p, c in rows.items() if c not in ("RESOLVED", "CLEAR")]
    print("SUMMARY  %s" % ", ".join("%s %d" % kv for kv in sorted(counts.items()) if kv[0] != "CLEAR"))
    if whack:
        print("WHACK_A_MOLE the change removed one contact and created or moved another: the cause is not "
              "one dimension. Check the path against the guide (hvp_pathfit.py) and the constraint count "
              "(catalog 3) before another attempt.")
    elif any(c == "MOVED" for c in rows.values()):
        print("NOTE     contact moved along the input: same pair, different place")
    print("RESULT   %s" % ("CLEAR_AFTER" if not remaining else "VIOLATIONS_REMAIN %d" % len(remaining)))
    if a.out:
        json.dump({"layer": "L2", "kind": "comparison (not a verdict)", "provenance": {"command": " ".join(sys.argv)},
                   "inputs": {os.path.relpath(f): C.sha256_file(f) for f in a.files},
                   "classes": rows, "counts": counts, "whack_a_mole": whack,
                   "before": before, "after": after}, open(a.out, "w"), indent=1, sort_keys=True)
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())

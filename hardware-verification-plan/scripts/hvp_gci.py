#!/usr/bin/env python3
"""Discretization error of a mesh/time-step study: Grid Convergence Index (GCI).

  hvp_gci.py --h 0.25,0.5,1.0 --f 101.2,103.9,114.6 [--max-gci 5] [--fs 1.25] [--out gci.json]
  hvp_gci.py --ev fine.json,medium.json,coarse.json --ptr /metrics/max_stress_MPa/value --h-ptr /mesh/h

Three solutions on systematically refined meshes (or time steps), h = representative cell size
(e.g. (volume / cells)^(1/3)) or time step, f = the quantity you judge on (peak stress at a
hot spot, pressure drop, natural frequency). Order does not matter; they are sorted fine->coarse.
Procedure: Celik et al., J. Fluids Eng. 130 (2008) 078001, as used in ASME V&V 20:
  observed order p from the three solutions (fixed-point iteration), Richardson-extrapolated
  value f_ext, fine-grid GCI = Fs * |(f1 - f2) / f1| / (r21^p - 1).

Output lines: P (observed order), F_EXT, GCI_FINE (percent), ASYMPTOTIC ratio (near 1 means the
meshes are in the asymptotic range), and warnings:
  OSCILLATORY      solutions do not change monotonically: GCI is not reliable; refine further
  RATIO_SMALL      refinement ratio < 1.3: differences are dominated by noise
  ORDER_ODD        observed p far from the method's formal order (pass --formal-order)
The JSON written with --out follows the verdict convention (status, metrics, scope...), so the
discretization error can be cited and compared like any other evidence.
Exit codes: 0 GCI within --max-gci (or no limit given), 1 above limit or unreliable, 3 input error.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def gci(h, f, fs=1.25):
    pts = sorted(zip(h, f))                       # fine first
    (h1, f1), (h2, f2), (h3, f3) = pts
    r21, r32 = h2 / h1, h3 / h2
    e21, e32 = f2 - f1, f3 - f2
    out = {"h": [h1, h2, h3], "f": [f1, f2, f3], "r21": r21, "r32": r32, "warnings": []}
    if min(r21, r32) < 1.3:
        out["warnings"].append("RATIO_SMALL")
    if e21 == 0 or e32 == 0:
        out.update(p=None, f_ext=f1, gci_fine=0.0 if e21 == 0 else None, asymptotic=None)
        if e21 != 0 or e32 != 0:
            out["warnings"].append("OSCILLATORY")
        return out
    s = math.copysign(1.0, e32 / e21)
    if s < 0:
        out["warnings"].append("OSCILLATORY")
    p = 2.0
    for _ in range(200):
        q = math.log((r21 ** p - s) / (r32 ** p - s))
        pn = abs(math.log(abs(e32 / e21)) + q) / math.log(r21)
        if abs(pn - p) < 1e-12:
            p = pn
            break
        p = pn
    f_ext = (r21 ** p * f1 - f2) / (r21 ** p - 1)
    ea21 = abs((f1 - f2) / f1) if f1 else float("inf")
    g21 = fs * ea21 / (r21 ** p - 1)
    ea32 = abs((f2 - f3) / f2) if f2 else float("inf")
    g32 = fs * ea32 / (r32 ** p - 1)
    out.update(p=p, f_ext=f_ext, gci_fine=100 * g21, gci_medium=100 * g32,
               asymptotic=(g32 / (r21 ** p * g21)) if g21 else None)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--h", help="three cell sizes or time steps, comma separated")
    ap.add_argument("--f", help="three solution values, same order as --h")
    ap.add_argument("--ev", help="three evidence JSON files (instead of --f)")
    ap.add_argument("--ptr", help="JSON pointer of the value in each --ev file")
    ap.add_argument("--h-ptr", help="JSON pointer of h in each --ev file (instead of --h)")
    ap.add_argument("--fs", type=float, default=1.25, help="safety factor (1.25 for 3 meshes)")
    ap.add_argument("--max-gci", type=float, help="limit for the fine-grid GCI in percent")
    ap.add_argument("--formal-order", type=float)
    ap.add_argument("--quantity", default="f")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        if a.ev:
            from hvp_cite import pointer
            files = a.ev.split(",")
            docs = [json.load(open(x)) for x in files]
            f = [float(pointer(d, a.ptr)) for d in docs]
            h = [float(pointer(d, a.h_ptr)) for d in docs] if a.h_ptr else [float(x) for x in a.h.split(",")]
        else:
            h = [float(x) for x in a.h.split(",")]
            f = [float(x) for x in a.f.split(",")]
        if len(h) != 3 or len(f) != 3 or min(h) <= 0:
            raise ValueError("need three positive h and three f")
    except Exception as e:  # noqa: BLE001
        print("INPUT_ERROR", e)
        return 3
    r = gci(h, f, a.fs)
    if a.formal_order and r.get("p") and abs(r["p"] - a.formal_order) > 0.5 * a.formal_order:
        r["warnings"].append("ORDER_ODD")
    print("MESHES   h fine->coarse %s  f %s  r21 %.3f r32 %.3f" % (r["h"], r["f"], r["r21"], r["r32"]))
    if r.get("p") is not None:
        print("P        %.4f" % r["p"])
        print("F_EXT    %.6g" % r["f_ext"])
        print("GCI_FINE %.4f %%   (fine-grid value %.6g is within about this band of the mesh-independent value)"
              % (r["gci_fine"], r["f"][0]))
        print("ASYMPTOTIC %.4f   (near 1: meshes are in the asymptotic range)" % r["asymptotic"] if r["asymptotic"] else
              "ASYMPTOTIC n/a")
    else:
        print("P        n/a (two solutions identical)")
    for w in r["warnings"]:
        print("WARNING ", w)
    unreliable = "OSCILLATORY" in r["warnings"] or r.get("gci_fine") is None
    over = a.max_gci is not None and not unreliable and r["gci_fine"] > a.max_gci
    status = "FAIL" if (unreliable or over) else "PASS"
    if a.out:
        m = {"value": r.get("gci_fine"), "limit": a.max_gci, "sense": "max"} if a.max_gci is not None else \
            {"value": r.get("gci_fine")}
        json.dump({"status": status, "layer": "L3",
                   "scope": "discretization error of %s over 3 refinements" % a.quantity,
                   "assumptions": ["systematic refinement", "monotone convergence", "Fs = %s" % a.fs],
                   "limitations": ["numerical error only; says nothing about model form or inputs"],
                   "provenance": {"command": " ".join(sys.argv)},
                   "metrics": {"gci_fine_percent": m},
                   "observed_order": r.get("p"), "extrapolated": r.get("f_ext"), "warnings": r["warnings"],
                   "h": r["h"], "f": r["f"]}, open(a.out, "w"), indent=1)
    print("RESULT", status + (" (unreliable: %s)" % ",".join(r["warnings"]) if unreliable else ""))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

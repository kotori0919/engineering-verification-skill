#!/usr/bin/env python3
"""Does a follower's path fit its guide? (pin in a slot or groove, cam follower, slider in a track)

  hvp_pathfit.py --path follower_path.csv --guide groove_centerline.csv --width 6.4 --pin-d 6.0
                 [--tol 0.1] [--write-guide results/guide_from_path.csv] [--out results/pathfit.json]

A pin that is moved by one mechanism (a lever, a slider, a linkage) and also held by a groove is
constrained twice. The two agree only if the groove's centre line is the path the mechanism
already gives the pin. If they disagree anywhere, the pin must cut the groove wall there: an
interference no local trim removes, because every trim changes where the mismatch shows.

--path   CSV s,x,y: the pin centre computed with the groove REMOVED (from the other constraints
         only: the kinematic solver of catalog 3, or the CAD assembly with the slot joint suppressed),
         s = input coordinate. Plane coordinates of the groove, same unit throughout.
--guide  CSV x,y: the groove centre line as a polyline, in order along the groove.
--width  groove width;  --pin-d pin (follower) diameter;  --tol positional tolerance per side
         (print shrink, hole position, play you do not want to use).
Allowed deviation a = (width - pin_d)/2 - tol. At each s: e = distance from the pin centre to the
centre line, signed (+ left of the guide direction, - right).

Result (one per run; numbers to judge by are printed with it)
  FITS        max e <= a over the whole path
  OFFSET      the path fits after one translation v of the guide (computed): placement or a
              mixed design generation, not shape. Fix the position, then rerun
  OVERTRAVEL  the pin leaves the guide beyond an end: the stroke is longer than the groove
  LOCAL       violations are near interior corners of the guide and cover < 15 % of the path:
              the corner shape does not follow the path's turn (blend or relieve that corner)
  MISMATCH    the guide's shape differs from the path: derive the guide from the path
              (--write-guide gives it), or remove one of the two constraints (catalog 3 counts them)
Always printed: max e and where, the width that would accept the current path
(pin_d + 2(max e + tol)), the best translation, and the violation intervals.
Limitations: 2D (the groove plane); out-of-plane error and pin tilt are not seen; the path is
only as good as the solver or CAD that produced it.
Exit codes: 0 FITS, 1 otherwise, 3 input error.
"""
import argparse
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def read_csv(path, cols):
    rows = []
    for r in csv.DictReader(open(path, newline="")):
        try:
            rows.append(tuple(float(r[c]) for c in cols))
        except (KeyError, ValueError) as e:
            raise SystemExit("INPUT_ERROR %s needs columns %s (%s)" % (path, ",".join(cols), e))
    return rows


def nearest(p, poly):
    """(distance, signed distance, foot point, segment index, t, beyond_end)."""
    best = None
    for i in range(len(poly) - 1):
        (x1, y1), (x2, y2) = poly[i], poly[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy or 1e-300
        t_raw = ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2
        t = min(1.0, max(0.0, t_raw))
        fx, fy = x1 + t * dx, y1 + t * dy
        d = math.hypot(p[0] - fx, p[1] - fy)
        if best is None or d < best[0] - 1e-12:
            side = dx * (p[1] - fy) - dy * (p[0] - fx)
            beyond = (i == 0 and t_raw < 0) or (i == len(poly) - 2 and t_raw > 1)
            best = (d, d if side >= 0 else -d, (fx, fy), i, t, beyond)
    return best


def evaluate(path, poly, a, shift=(0.0, 0.0)):
    res = []
    for s, x, y in path:
        d, sd, foot, i, t, beyond = nearest((x - shift[0], y - shift[1]), poly)
        res.append({"s": s, "e": d, "e_signed": sd, "seg": i, "t": t, "beyond_end": beyond,
                    "foot": foot, "p": (x - shift[0], y - shift[1]), "violates": d > a})
    return res


def best_shift(path, poly, rounds=300):
    """Translation of the guide that best matches the path (point-to-polyline ICP, least squares)."""
    v = (0.0, 0.0)
    for _ in range(rounds):
        r = evaluate(path, poly, float("inf"), v)
        dx = sum(z["p"][0] - z["foot"][0] for z in r) / len(r)
        dy = sum(z["p"][1] - z["foot"][1] for z in r) / len(r)
        v = (v[0] + dx, v[1] + dy)
        if math.hypot(dx, dy) < 1e-9:
            break
    return v


def intervals(res):
    out, cur = [], None
    for z in res:
        if z["violates"]:
            if cur is None:
                cur = {"s_start": z["s"], "s_end": z["s"], "max_e": z["e"], "s_max": z["s"], "samples": 0,
                       "sides": set(), "beyond_end": False}
            cur["s_end"] = z["s"]
            cur["samples"] += 1
            cur["sides"].add("+" if z["e_signed"] >= 0 else "-")
            cur["beyond_end"] |= z["beyond_end"]
            if z["e"] > cur["max_e"]:
                cur["max_e"], cur["s_max"] = z["e"], z["s"]
        elif cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    for c in out:
        c["sides"] = "".join(sorted(c["sides"]))
    return out


def near_corner(z, poly, radius):
    for k in range(1, len(poly) - 1):
        if math.hypot(z["p"][0] - poly[k][0], z["p"][1] - poly[k][1]) <= radius:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", required=True)
    ap.add_argument("--guide", required=True)
    ap.add_argument("--width", type=float, required=True)
    ap.add_argument("--pin-d", type=float, required=True)
    ap.add_argument("--tol", type=float, default=0.0)
    ap.add_argument("--local-frac", type=float, default=0.15)
    ap.add_argument("--write-guide")
    ap.add_argument("--out")
    a = ap.parse_args()
    path = sorted(read_csv(a.path, ("s", "x", "y")))
    poly = read_csv(a.guide, ("x", "y"))
    if len(path) < 2 or len(poly) < 2:
        print("INPUT_ERROR need at least 2 path samples and 2 guide points")
        return 3
    allow = (a.width - a.pin_d) / 2.0 - a.tol
    if allow < 0:
        print("NOTE     width - pin_d - 2 tol < 0: no play at all; every sample violates")
    res = evaluate(path, poly, allow)
    ivs = intervals(res)
    worst = max(res, key=lambda z: z["e"])
    nviol = sum(z["violates"] for z in res)
    frac = nviol / len(res)
    v = best_shift(path, poly)
    res_v = evaluate(path, poly, allow, v)
    worst_v = max(res_v, key=lambda z: z["e"])

    if nviol == 0:
        result = "FITS"
    elif worst_v["e"] <= allow and math.hypot(*v) > 0:
        result = "OFFSET"
    elif any(z["beyond_end"] and z["violates"] for z in res):
        result = "OVERTRAVEL"
    elif frac < a.local_frac and all(near_corner(z, poly, a.width * 2) for z in res if z["violates"]):
        result = "LOCAL"
    else:
        result = "MISMATCH"

    print("PATH     %d samples, s %g .. %g      GUIDE %d points" % (len(path), path[0][0], path[-1][0], len(poly)))
    print("ALLOWED  a = (%g - %g)/2 - %g = %.4g" % (a.width, a.pin_d, a.tol, allow))
    print("MAX_E    %.4g at s=%g (%s side%s)" % (worst["e"], worst["s"], "+" if worst["e_signed"] >= 0 else "-",
                                                ", beyond the guide end" if worst["beyond_end"] else ""))
    print("VIOLATES %d of %d samples (%.0f %%)" % (nviol, len(res), 100 * frac))
    for iv in ivs:
        print("  s %g .. %g   max e %.4g at s=%g   side %s%s" % (iv["s_start"], iv["s_end"], iv["max_e"], iv["s_max"],
                                                              iv["sides"], "   BEYOND_END" if iv["beyond_end"] else ""))
    need_w = a.pin_d + 2 * (worst["e"] + a.tol)
    print("WIDTH    width that accepts the current path: %.4g = pin_d + 2(max e + tol); where the pin runs on "
          "the centre line it then has +-%.4g of play (judge that backlash before choosing it)" % (need_w, worst["e"]))
    print("SHIFT    best translation of the guide (%.4g, %.4g), |v| %.4g -> max e %.4g" % (
        v[0], v[1], math.hypot(*v), worst_v["e"]))
    print("RESULT   %s" % result)
    hint = {"OFFSET": "move the guide (or the part carrying it) by the SHIFT vector, then check why it was off "
                      "(placement, mixed design generation, catalog 1)",
            "OVERTRAVEL": "the stroke runs past the guide end: extend the guide or add a stop inside it",
            "LOCAL": "the guide's corner does not follow the path's turn: blend or relieve that corner only",
            "MISMATCH": "derive the guide from the path (--write-guide) or remove one of the two constraints; "
                        "trimming walls will move the contact, not remove it"}.get(result)
    if hint:
        print("NEXT     " + hint)
    if a.write_guide:
        with open(a.write_guide, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["x", "y"])
            for _, x, y in path:
                w.writerow(["%.6f" % x, "%.6f" % y])
        print("WROTE    %s (centre line = the path; offset by width/2 each side for the groove walls)" % a.write_guide)
    if a.out:
        ev = {"status": "PASS" if result == "FITS" else "FAIL", "layer": "L2",
              "method": "follower path vs guide centre line (2D)",
              "scope": "pin centre path %s against guide %s" % (a.path, a.guide),
              "assumptions": ["the path was computed with the guide constraint removed",
                              "width, pin diameter and tolerance as given"],
              "limitations": ["2D in the guide plane; out-of-plane error and pin tilt not seen"],
              "inputs": {os.path.relpath(p): C.sha256_file(p) for p in (a.path, a.guide)},
              "provenance": {"command": " ".join(sys.argv)},
              "metrics": {"max_deviation": {"value": worst["e"], "limit": allow, "sense": "max"}},
              "result": result, "shift": {"dx": v[0], "dy": v[1], "max_e_after": worst_v["e"]},
              "width_to_accept": need_w,
              "data": {"where": {"s": worst["s"]}, "intervals": ivs, "violating_fraction": frac}}
        json.dump(ev, open(a.out, "w"), indent=1, sort_keys=True)
    return 0 if result == "FITS" else 1


if __name__ == "__main__":
    sys.exit(main())

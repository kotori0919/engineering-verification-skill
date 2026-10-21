#!/usr/bin/env python3
"""Fluid-flow first-pass oracle: pressure drop of a pipe/channel network segment (SI units).

  hvp_flow.py pipe --D 0.006 --L 0.5 --Q 2e-5 [--rho 998 --mu 1.0e-3] [--eps 1.5e-6] [--K 1.5]
                   [--dp-max 20000] [--out flow.json]

  D hydraulic diameter m, L length m, Q volume flow m^3/s, rho kg/m^3, mu Pa·s,
  eps wall roughness m, K sum of minor-loss coefficients (bends, fittings, entry/exit).
Prints Reynolds number and regime, friction factor and how it was obtained, and the pressure drop:
  laminar  (Re < 2300)  f = 64/Re  (Hagen-Poiseuille: ΔP = 128 μ L Q / (π D^4))
  turbulent (Re > 4000) Colebrook-White solved iteratively, cross-checked against Haaland;
                        the two must agree within 3 % or a warning is printed
  transitional          both laminar and turbulent values are printed; the result uses the larger
                        (conservative for a pressure-drop limit) and is flagged TRANSITIONAL
Use it to size channels and to check CFD: a CFD pressure drop in a straight section that differs
from this by more than the stated uncertainty means the mesh, boundary conditions or model need
attention before anything else is trusted (see oracle-catalog 37).
Exit codes: 0 PASS (or no --dp-max), 1 above --dp-max, 3 input error.
"""
import argparse
import json
import math
import sys


def friction(Re, rel):
    if Re < 2300:
        return 64.0 / Re, "laminar 64/Re", None
    hl = (-1.8 * math.log10((rel / 3.7) ** 1.11 + 6.9 / Re)) ** -2
    f = hl
    for _ in range(100):
        fn = (-2.0 * math.log10(rel / 3.7 + 2.51 / (Re * math.sqrt(f)))) ** -2
        if abs(fn - f) < 1e-14:
            f = fn
            break
        f = fn
    return f, "Colebrook-White (iterative)", hl


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="op", required=True)
    p = sub.add_parser("pipe")
    for k, d in (("D", None), ("L", None), ("Q", None), ("rho", 998.0), ("mu", 1.0e-3), ("eps", 0.0), ("K", 0.0),
                 ("dp_max", None)):
        p.add_argument("--" + k.replace("_", "-"), dest=k, type=float, default=d)
    p.add_argument("--out")
    a = ap.parse_args()
    if None in (a.D, a.L, a.Q) or a.D <= 0 or a.Q <= 0:
        print("INPUT_ERROR --D, --L, --Q required and positive")
        return 3
    A = math.pi * a.D ** 2 / 4
    V = a.Q / A
    Re = a.rho * V * a.D / a.mu
    rel = a.eps / a.D
    warns = []
    if 2300 <= Re <= 4000:
        f_l = 64.0 / Re
        f_t, _, hl = friction(Re, rel)
        f, how = max(f_l, f_t), "TRANSITIONAL: max(laminar %.5g, turbulent %.5g)" % (f_l, f_t)
        warns.append("TRANSITIONAL regime: friction factor uncertain; measure or avoid this range")
    else:
        f, how, hl = friction(Re, rel)
        if hl is not None and abs(hl - f) / f > 0.03:
            warns.append("Colebrook %.5g and Haaland %.5g differ by more than 3 %%" % (f, hl))
    q = 0.5 * a.rho * V ** 2
    dp_f = f * a.L / a.D * q
    dp_k = a.K * q
    dp = dp_f + dp_k
    print("FLOW     D %g m, L %g m, Q %g m^3/s -> V %.5g m/s; rho %g, mu %g" % (a.D, a.L, a.Q, V, a.rho, a.mu))
    print("RE       %.6g (%s)" % (Re, "laminar" if Re < 2300 else "turbulent" if Re > 4000 else "transitional"))
    print("FRICTION f = %.6g  [%s]%s" % (f, how, "  Haaland %.6g" % hl if hl is not None and Re > 4000 else ""))
    if Re < 2300:
        hp = 128 * a.mu * a.L * a.Q / (math.pi * a.D ** 4)
        print("CHECK    Hagen-Poiseuille 128 μ L Q / (π D^4) = %.6g Pa (friction part %.6g Pa)" % (hp, dp_f))
    print("DP       friction %.6g Pa + minor (K %g) %.6g Pa = %.6g Pa" % (dp_f, a.K, dp_k, dp))
    for w in warns:
        print("WARNING  " + w)
    st = None if a.dp_max is None else ("PASS" if dp <= a.dp_max else "FAIL")
    print("RESULT   %s" % (st or "no --dp-max given"))
    if a.out:
        json.dump({"status": st, "layer": "L2",
                   "scope": "pressure drop of one straight segment plus minor losses",
                   "assumptions": ["incompressible, Newtonian, fully developed", "rho %g, mu %g" % (a.rho, a.mu),
                                   "roughness %g m" % a.eps, "sum K %g" % a.K],
                   "limitations": ["no entrance-length effects", "no particles or two-phase flow",
                                   "K values from handbook, not measured"] + warns,
                   "provenance": {"command": " ".join(sys.argv)},
                   "metrics": {"pressure_drop_Pa": {"value": dp, "limit": a.dp_max, "sense": "max"}}
                   if a.dp_max is not None else {"pressure_drop_Pa": {"value": dp}},
                   "Re": Re, "friction_factor": f, "velocity_m_s": V}, open(a.out, "w"), indent=1)
    return 0 if st in (None, "PASS") else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fatigue and fracture first-pass oracles (closed form, headless, standard library).

Units: stress MPa, crack length m, stress intensity MPa·sqrt(m).

  hvp_fatigue.py rainflow --series load.csv [--column 1]       cycle counting (ASTM E1049-85 3-point)
  hvp_fatigue.py rainflow --values 0,10,4,6,0          (negative first value: --values=-5,3,...)

  hvp_fatigue.py damage --series stress.csv | --values ...      Palmgren-Miner damage from a stress history
        --basquin A,b              S-N curve S_a = A * N^b (b < 0), or
        --sn 300:1e3,150:1e5,90:1e7   S-N points (log-log interpolation)
        [--su 50] [--mean goodman|gerber|swt|none]   mean-stress correction (needs --su for goodman/gerber)
        [--se 20]                  endurance limit: amplitudes below it do no damage (omit for none)
        [--kf 1.0] [--repeats 1e5] [--dmax 0.3] [--out fatigue.json]

  hvp_fatigue.py paris --C 1e-11 --m 3 --Y 1.12 --dsigma 80 --a0 0.0005 (--ac 0.005 | --kic 30 --smax 100)
        cycles for a crack to grow from a0 to ac (da/dN = C ΔK^m, ΔK = Y Δσ sqrt(pi a), constant Y)
        [--life-target 1e6] [--out paris.json]

  hvp_fatigue.py lefm --Y 1.0 --sigma 100 --a 0.01 --kic 30 [--sy 55 --thickness 0.006] [--out k.json]
        K_I = Y σ sqrt(pi a), margin K_IC / K_I, critical crack length, plane-strain validity

Every subcommand prints the formula it used and its inputs, and --out writes evidence in the
verdict convention (status, metrics, scope, assumptions, limitations, provenance).
These are screening oracles: S-N data, mean-stress model, Y(a) and K_IC must come from your
material and geometry, with their sources recorded. Printed and moulded polymers need
orientation-specific data; a datasheet value for the bulk polymer is not enough.
Exit codes: 0 PASS (or no criterion), 1 below criterion, 3 input error.
"""
import argparse
import json
import math
import sys


# ---------------------------------------------------------------- rainflow (ASTM E1049-85 5.4.4)

def reversals(xs):
    out = []
    for x in xs:
        if out and x == out[-1]:
            continue
        if len(out) >= 2 and (out[-1] - out[-2]) * (x - out[-1]) > 0:
            out[-1] = x                       # same direction: extend
        else:
            out.append(x)
    return out


def rainflow(xs):
    """[(range, mean, count)] with count 1.0 (full) or 0.5 (half)."""
    pts = reversals(xs)
    stack, cycles = [], []
    for p in pts:
        stack.append(p)
        while len(stack) >= 3:
            x = abs(stack[-1] - stack[-2])
            y = abs(stack[-2] - stack[-3])
            if x < y:
                break
            if len(stack) == 3:               # Y contains the starting point
                cycles.append((y, (stack[0] + stack[1]) / 2, 0.5))
                stack.pop(0)
            else:
                cycles.append((y, (stack[-2] + stack[-3]) / 2, 1.0))
                last = stack.pop()
                stack.pop()
                stack.pop()
                stack.append(last)
    for i in range(len(stack) - 1):
        cycles.append((abs(stack[i + 1] - stack[i]), (stack[i + 1] + stack[i]) / 2, 0.5))
    return cycles


# ---------------------------------------------------------------- S-N, mean stress, Miner

def equivalent_amplitude(sa, sm, mode, su):
    if mode == "none" or sm == 0:
        return sa
    if mode == "goodman":
        if sm >= su:
            return math.inf
        return sa / (1 - sm / su) if sm > 0 else sa      # compressive mean: no benefit taken
    if mode == "gerber":
        if sm >= su:
            return math.inf
        return sa / (1 - (sm / su) ** 2) if sm > 0 else sa
    if mode == "swt":
        smax = sm + sa
        return math.sqrt(max(smax, 0.0) * sa) if smax > 0 else 0.0
    raise ValueError(mode)


def cycles_to_failure(sa, basquin=None, sn=None):
    if sa <= 0:
        return math.inf
    if basquin:
        A, b = basquin
        return (sa / A) ** (1.0 / b)
    pts = sorted(sn, key=lambda t: -t[0])               # high stress first
    if sa >= pts[0][0]:
        (s1, n1), (s2, n2) = pts[0], pts[1]
    elif sa <= pts[-1][0]:
        (s1, n1), (s2, n2) = pts[-2], pts[-1]
    else:
        for (s1, n1), (s2, n2) in zip(pts, pts[1:]):
            if s2 <= sa <= s1:
                break
    k = (math.log10(n2) - math.log10(n1)) / (math.log10(s2) - math.log10(s1))
    return 10 ** (math.log10(n1) + k * (math.log10(sa) - math.log10(s1)))


# ---------------------------------------------------------------- fracture

def k_i(Y, sigma, a):
    return Y * sigma * math.sqrt(math.pi * a)


def paris_cycles(C, m, Y, ds, a0, ac):
    g = C * (Y * ds * math.sqrt(math.pi)) ** m
    if abs(m - 2) < 1e-12:
        return math.log(ac / a0) / g
    e = 1 - m / 2
    return (ac ** e - a0 ** e) / (e * g)


def paris_numeric(C, m, Y, ds, a0, ac, steps=20000):
    # log-spaced trapezoid integration of dN = da / (C ΔK^m), as an independent check
    n, la0, la1 = 0.0, math.log(a0), math.log(ac)
    h = (la1 - la0) / steps
    f = lambda a: a / (C * k_i(Y, ds, a) ** m)            # noqa: E731  (da = a dln a)
    prev = f(a0)
    for i in range(1, steps + 1):
        cur = f(math.exp(la0 + i * h))
        n += 0.5 * (prev + cur) * h
        prev = cur
    return n


# ---------------------------------------------------------------- io

def read_series(a):
    if a.values:
        return [float(x) for x in a.values.split(",") if x.strip()]
    xs = []
    for line in open(a.series):
        parts = [p.strip() for p in line.replace(";", ",").split(",")]
        try:
            xs.append(float(parts[a.column]))
        except (ValueError, IndexError):
            continue                                        # header or blank
    return xs


def write(a, doc):
    if getattr(a, "out", None):
        doc["provenance"] = {"command": " ".join(sys.argv)}
        json.dump(doc, open(a.out, "w"), indent=1)


def status_of(value, limit, sense):
    if limit is None:
        return None
    ok = value <= limit if sense == "max" else value >= limit
    return "PASS" if ok else "FAIL"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="op", required=True)
    r = sub.add_parser("rainflow")
    d = sub.add_parser("damage")
    for p in (r, d):
        p.add_argument("--series")
        p.add_argument("--values")
        p.add_argument("--column", type=int, default=0)
        p.add_argument("--out")
    d.add_argument("--basquin")
    d.add_argument("--sn")
    d.add_argument("--su", type=float)
    d.add_argument("--mean", default="goodman", choices=["goodman", "gerber", "swt", "none"])
    d.add_argument("--se", type=float)
    d.add_argument("--kf", type=float, default=1.0)
    d.add_argument("--repeats", type=float, default=1.0, help="how many times the history repeats in service")
    d.add_argument("--dmax", type=float, help="allowed damage (e.g. 0.3 for scatter in Miner's rule)")
    pa = sub.add_parser("paris")
    for k in ("C", "m", "Y", "dsigma", "a0", "ac", "kic", "smax", "life_target"):
        pa.add_argument("--" + k.replace("_", "-"), dest=k, type=float)
    pa.add_argument("--out")
    le = sub.add_parser("lefm")
    for k in ("Y", "sigma", "a", "kic", "sy", "thickness", "min_margin"):
        le.add_argument("--" + k.replace("_", "-"), dest=k, type=float)
    le.add_argument("--out")
    a = ap.parse_args()

    try:
        if a.op in ("rainflow", "damage"):
            xs = read_series(a)
            if len(xs) < 2:
                raise ValueError("need at least two points")
            cyc = rainflow(xs)
        if a.op == "rainflow":
            full = sum(c for _, _, c in cyc)
            print("RAINFLOW ASTM E1049-85 three-point; %d reversals -> %g equivalent cycles" % (len(reversals(xs)), full))
            print("  %-12s %-12s %s" % ("range", "mean", "count"))
            for rg, mn, c in sorted(cyc, key=lambda t: -t[0]):
                print("  %-12.6g %-12.6g %g" % (rg, mn, c))
            write(a, {"layer": "L3", "scope": "rainflow cycle count of the given history",
                      "assumptions": ["history is representative of service"], "limitations": ["counting only"],
                      "cycles": [{"range": rg, "mean": mn, "count": c} for rg, mn, c in cyc]})
            return 0

        if a.op == "damage":
            if not (a.basquin or a.sn):
                raise ValueError("give --basquin A,b or --sn S:N,...")
            if a.mean in ("goodman", "gerber") and a.su is None:
                raise ValueError("--su needed for %s" % a.mean)
            basq = tuple(float(x) for x in a.basquin.split(",")) if a.basquin else None
            sn = [tuple(float(y) for y in p.split(":")) for p in a.sn.split(",")] if a.sn else None
            D, rows = 0.0, []
            for rg, mn, c in cyc:
                sa = a.kf * rg / 2
                sm = a.kf * mn
                seq = equivalent_amplitude(sa, sm, a.mean, a.su)
                if a.se is not None and seq < a.se:
                    n = math.inf
                else:
                    n = 0.0 if math.isinf(seq) else cycles_to_failure(seq, basq, sn)
                dd = (c / n) if n not in (0.0,) and not math.isinf(n) else (math.inf if n == 0.0 else 0.0)
                D += dd
                rows.append({"Sa": sa, "Sm": sm, "Sa_eq": seq, "count": c, "N": n, "d": dd})
            Dt = D * a.repeats
            life = (1.0 / D) if D > 0 else math.inf
            print("DAMAGE   Palmgren-Miner D = sum(n_i / N_i); mean-stress %s%s; Kf %g; endurance %s" % (
                a.mean, " (Su %g)" % a.su if a.su else "", a.kf, a.se if a.se is not None else "none"))
            print("S-N      %s" % ("Basquin S_a = %g N^%g" % basq if basq else "points %s (log-log)" % a.sn))
            for x in sorted(rows, key=lambda t: -t["Sa_eq"])[:20]:
                print("  Sa %-9.4g Sm %-9.4g Sa_eq %-9.4g n %-5g N %-11.4g d %.4g" % (
                    x["Sa"], x["Sm"], x["Sa_eq"], x["count"], x["N"], x["d"]))
            print("D_HISTORY %.6g   (one pass of the history)" % D)
            print("D_SERVICE %.6g   (x %g repeats)" % (Dt, a.repeats))
            print("LIFE     %.6g repeats of the history to D = 1" % life)
            st = status_of(Dt, a.dmax, "max")
            print("RESULT   %s" % (st or "no --dmax given"))
            write(a, {"status": st, "layer": "L3",
                      "scope": "fatigue damage of the given stress history x %g repeats" % a.repeats,
                      "assumptions": ["linear damage accumulation (Palmgren-Miner)", "mean-stress model %s" % a.mean,
                                      "S-N %s" % (a.basquin or a.sn), "Kf %g" % a.kf],
                      "limitations": ["Miner scatter: failures observed from D about 0.3 to 3",
                                      "no sequence effects", "uniaxial; multiaxial needs critical-plane methods"],
                      "metrics": {"damage": {"value": Dt, "limit": a.dmax, "sense": "max"}} if a.dmax is not None
                      else {"damage": {"value": Dt}}, "cycles": rows})
            return 0 if st in (None, "PASS") else 1

        if a.op == "paris":
            for k in ("C", "m", "Y", "dsigma", "a0"):
                if getattr(a, k) is None:
                    raise ValueError("--%s required" % k)
            ac = a.ac
            if ac is None:
                if a.kic is None or a.smax is None:
                    raise ValueError("give --ac or --kic with --smax")
                ac = (a.kic / (a.Y * a.smax)) ** 2 / math.pi
            if ac <= a.a0:
                raise ValueError("a_c %.4g <= a0 %.4g: the initial crack is already critical" % (ac, a.a0))
            N = paris_cycles(a.C, a.m, a.Y, a.dsigma, a.a0, ac)
            Nn = paris_numeric(a.C, a.m, a.Y, a.dsigma, a.a0, ac)
            print("PARIS    da/dN = C ΔK^m, ΔK = Y Δσ sqrt(pi a); C %g, m %g, Y %g, Δσ %g MPa" % (a.C, a.m, a.Y, a.dsigma))
            print("CRACK    a0 %.6g m -> a_c %.6g m%s" % (a.a0, ac, " (from K_IC %g at σ_max %g)" % (a.kic, a.smax)
                                                      if a.ac is None else ""))
            print("CYCLES   %.6g (closed form)   %.6g (numeric check, rel diff %.2g)" % (N, Nn, abs(N - Nn) / N))
            st = status_of(N, a.life_target, "min")
            print("RESULT   %s" % (st or "no --life-target given"))
            write(a, {"status": st, "layer": "L3", "scope": "crack growth life from a0 to a_c under constant-amplitude Δσ",
                      "assumptions": ["LEFM, small-scale yielding", "constant geometry factor Y", "Paris regime only",
                                      "no threshold, no closure, no load sequence effects"],
                      "limitations": ["Y usually varies with a: use Y(a) integration for real geometry",
                                      "C, m must come from tests of this material and environment"],
                      "metrics": {"cycles_to_critical": {"value": N, "limit": a.life_target, "sense": "min"}}
                      if a.life_target else {"cycles_to_critical": {"value": N}},
                      "a0": a.a0, "ac": ac, "numeric_check": Nn})
            return 0 if st in (None, "PASS") else 1

        if a.op == "lefm":
            for k in ("Y", "sigma", "a", "kic"):
                if getattr(a, k) is None:
                    raise ValueError("--%s required" % k)
            K = k_i(a.Y, a.sigma, a.a)
            margin = a.kic / K
            ac = (a.kic / (a.Y * a.sigma)) ** 2 / math.pi
            print("LEFM     K_I = Y σ sqrt(pi a) = %g x %g x sqrt(pi x %g) = %.6g MPa·sqrt(m)" % (a.Y, a.sigma, a.a, K))
            print("MARGIN   K_IC / K_I = %g / %.6g = %.6g" % (a.kic, K, margin))
            print("CRITICAL a_c = (K_IC / (Y σ))^2 / pi = %.6g m" % ac)
            warns = []
            if a.sy:
                req = 2.5 * (a.kic / a.sy) ** 2
                ok = a.a >= req and (a.thickness is None or a.thickness >= req)
                print("VALIDITY plane strain needs a, B >= 2.5 (K_IC/σ_y)^2 = %.6g m: %s" % (req, "met" if ok else "NOT met"))
                if not ok:
                    warns.append("plane-strain/small-scale-yielding condition not met: LEFM may be unconservative or "
                                 "invalid; use J-integral / FAD (BS 7910, API 579)")
                if a.sigma > 0.8 * a.sy:
                    warns.append("net stress above 0.8 σ_y: plastic collapse may govern; check with an FAD")
            for w in warns:
                print("WARNING  " + w)
            st = status_of(margin, a.min_margin, "min")
            print("RESULT   %s" % (st or "no --min-margin given"))
            write(a, {"status": st, "layer": "L3", "scope": "fracture check of one crack, mode I",
                      "assumptions": ["LEFM", "Y = %g" % a.Y, "K_IC = %g MPa·sqrt(m)" % a.kic],
                      "limitations": ["mode I only", "static load"] + warns,
                      "metrics": {"k_margin": {"value": margin, "limit": a.min_margin, "sense": "min"}}
                      if a.min_margin else {"k_margin": {"value": margin}}, "K_I": K, "a_c": ac})
            return 0 if st in (None, "PASS") else 1
    except (ValueError, OSError) as e:
        print("INPUT_ERROR", e)
        return 3


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Which items cause the failure? Ablation by delta debugging (ddmin), with controls.

  hvp_isolate.py --items contacts.txt --cmd "python3 sim/stroke.py --contacts {items}" \
                 [--expect "STALLED"] [--all-causes] [--known A--B,C--D] [--repeat 2]
                 [--timeout 600] [--report results/isolate_od01.json]

Use it when a check fails but does not say what makes it fail: a motion stops with contacts
on, a solver reports "blocked", an assembly will not close. Items are what can be switched off
one by one: contact pairs, bodies, joints, features, parameter overrides. --items is a text file
with one item per line ('#' comments allowed).

The command runs once per subset. It receives the subset as a file path ({items} in --cmd and
$HVP_ITEMS, one item per line) and must exit 0 when the failure does NOT occur and non-zero
when it does. --expect STRING: a non-zero run counts as the failure only when its output contains
STRING; other non-zero runs are UNRESOLVED (a crash is not the defect). Runs happen in the
current directory; the command must not edit tracked files (a changed tree is reported).

Controls, run first:
  all items   must fail       else NOT_REPRODUCED (nothing to isolate)
  no items    must pass       else FAILS_WITHOUT_ITEMS: the cause is not among these items
                              (a joint, a drive limit, an over-constraint, the input itself)
  --repeat N  each control N times; different outcomes -> FLAKY (fix determinism first)
Then ddmin (Zeller & Hildebrandt 2002) finds a 1-minimal failing subset: with all of it the
failure occurs, and removing any single member makes it go away. Both are confirmed by fresh
reruns, not taken from the search's cache.
--all-causes: remove the subset found and repeat while the rest still fails. More than one
independent cause explains "fixed one contact, the next one appears".
--known: the answer you expect (an already solved case). Reports MATCHES_KNOWN or DIFFERS_FROM_KNOWN,
so the method is shown to work on a solved case before it is trusted on an open one.

Results: ISOLATED, NOT_REPRODUCED, FAILS_WITHOUT_ITEMS, FLAKY. Every run (subset, exit code,
outcome, output tail) is in the report, so each step can be rerun by hand.
Exit codes: 0 ISOLATED (and matches --known if given), 1 otherwise, 3 setup error.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def read_items(path):
    out = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if line and line not in out:
            out.append(line)
    return out


class Runner:
    def __init__(self, cmd, expect, timeout, tmp, order):
        self.cmd, self.expect, self.timeout, self.tmp = cmd, expect, timeout, tmp
        self.order = {x: i for i, x in enumerate(order)}
        self.cache, self.trials = {}, []

    def key(self, subset):
        return tuple(sorted(set(subset), key=self.order.get))

    def run(self, subset, label="", fresh=False):
        k = self.key(subset)
        if k in self.cache and not fresh:
            return self.cache[k]
        n = len(self.trials) + 1
        f = os.path.join(self.tmp, "items_%04d.txt" % n)
        with open(f, "w") as fh:
            fh.write("".join(x + "\n" for x in k))
        cmd = self.cmd.replace("{items}", f)
        try:
            p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=self.timeout,
                               env=dict(os.environ, HVP_ITEMS=f))
            rc, out = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            rc, out = "timeout", ""
        if rc == 0:
            outcome = "PASS"
        elif rc != "timeout" and (not self.expect or self.expect in out):
            outcome = "FAIL"
        else:
            outcome = "UNRESOLVED"
        self.trials.append({"n": n, "label": label, "items": list(k), "rc": rc, "outcome": outcome,
                            "command": cmd, "output_tail": out[-600:]})
        self.cache[k] = outcome
        return outcome


def split(lst, n):
    out, start = [], 0
    for i in range(n):
        end = start + (len(lst) - start) // (n - i)
        out.append(lst[start:end])
        start = end
    return [x for x in out if x]


def ddmin(items, R):
    """Zeller's ddmin. R.run(subset) == 'FAIL' is the failing test."""
    c, n = list(items), 2
    while len(c) >= 2:
        parts = split(c, n)
        reduced = False
        for p in parts:
            if R.run(p, "ddmin subset") == "FAIL":
                c, n, reduced = p, 2, True
                break
        if not reduced:
            for p in parts:
                comp = [x for x in c if x not in p]
                if comp and R.run(comp, "ddmin complement") == "FAIL":
                    c, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(c):
                break
            n = min(len(c), 2 * n)
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", required=True)
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--expect", default="")
    ap.add_argument("--all-causes", action="store_true")
    ap.add_argument("--known", default="", help="comma-separated items expected (solved case); ';' separates causes")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--max-runs", type=int, default=500)
    ap.add_argument("--report")
    a = ap.parse_args()
    items = read_items(a.items)
    if not items:
        print("INPUT_ERROR no items in %s" % a.items)
        return 3
    try:
        top = C.toplevel()
        before = C.git("status", "--porcelain", cwd=top)
    except (Exception, SystemExit):  # noqa: BLE001  (not in a git repository)
        top, before = None, None
    tmp = tempfile.mkdtemp(prefix="hvp-iso-")
    R = Runner(a.cmd, a.expect, a.timeout, tmp, items)

    def control(subset, label):
        res = [R.run(subset, label, fresh=i > 0) for i in range(max(1, a.repeat))]
        return res[0] if len(set(res)) == 1 else "FLAKY"

    full, empty = control(items, "control: all items"), control([], "control: no items")
    causes, result, more = [], None, False
    if "FLAKY" in (full, empty):
        result = "FLAKY"
    elif full != "FAIL":
        result = "NOT_REPRODUCED"
    elif empty == "FAIL":
        result = "FAILS_WITHOUT_ITEMS"
    else:
        rest = list(items)
        while True:
            m = ddmin(rest, R)
            # confirm with fresh runs (not the cache): the set fails, and each one-smaller set does not
            again = R.run(m, "confirm: minimal set again", fresh=True)
            minimal = all(R.run([x for x in m if x != y], "confirm: without %s" % y, fresh=True) != "FAIL" for y in m)
            causes.append({"items": m, "one_minimal": minimal, "reconfirmed": again == "FAIL"})
            rest = [x for x in rest if x not in m]
            if not rest or R.run(rest, "rest after removing causes") != "FAIL":
                break
            if not a.all_causes:
                more = True
                break
            if len(R.trials) > a.max_runs:
                break
        result = "ISOLATED"
        if a.all_causes and rest and R.cache.get(R.key(rest)) == "FAIL":
            print("WARNING  --max-runs reached with the rest still failing: more causes may remain")
    changed = None
    if top is not None:
        changed = C.git("status", "--porcelain", cwd=top) != before

    known, match = None, None
    if a.known and result == "ISOLATED":
        known = [sorted(x.strip() for x in grp.split(",") if x.strip()) for grp in a.known.split(";") if grp.strip()]
        found = sorted(sorted(c["items"]) for c in causes)
        match = sorted(known) == found

    print("ITEMS    %d   runs %d (each one in the report)" % (len(items), len(R.trials)))
    print("CONTROL  all items: %s   no items: %s" % (full, empty))
    for i, c in enumerate(causes, 1):
        print("CAUSE %d  %s%s%s" % (i, " + ".join(c["items"]), "" if c["one_minimal"] else "   (NOT 1-minimal on rerun)",
                                    "" if c["reconfirmed"] else "   (did NOT fail again on rerun: FLAKY)"))
    unresolved = sum(1 for t in R.trials if t["outcome"] == "UNRESOLVED")
    if unresolved:
        print("WARNING  %d run(s) UNRESOLVED (non-zero without --expect text, or timeout): see the report" % unresolved)
    if changed:
        print("WARNING  TREE_CHANGED the command modified the working tree")
    if known is not None:
        print("KNOWN    %s -> %s" % ("; ".join(",".join(k) for k in known), "MATCHES_KNOWN" if match else "DIFFERS_FROM_KNOWN"))
    print("RESULT   %s" % result)
    if result == "FAILS_WITHOUT_ITEMS":
        print("NOTE     the failure occurs with every listed item off: look at joints, limits, constraints, the input")
    if more:
        print("NOTE     MORE_CAUSES the items left after removing this cause still fail: rerun with --all-causes")
    if len(causes) > 1:
        print("NOTE     %d independent causes: fixing one leaves the failure in place; fix them as one issue" % len(causes))
    if a.report:
        rep = {"layer": "L2", "kind": "diagnosis (not a design verdict)", "method": "ablation by delta debugging (ddmin) with controls",
               "scope": "which of %d listed items are needed for the failure of: %s" % (len(items), a.cmd),
               "assumptions": ["the command's exit code reflects the failure", "items act independently of order"],
               "limitations": ["a cause outside the listed items is only detected as FAILS_WITHOUT_ITEMS",
                               "1-minimal is not globally minimal"],
               "provenance": {"command": " ".join(sys.argv)},
               "result": result, "controls": {"all": full, "none": empty}, "causes": causes, "more_causes": more,
               "known": known, "matches_known": match, "tree_changed": changed, "trials": R.trials}
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        json.dump(rep, open(a.report, "w"), indent=1)
    return 0 if result == "ISOLATED" and match is not False else 1


if __name__ == "__main__":
    sys.exit(main())

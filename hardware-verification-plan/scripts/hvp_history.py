#!/usr/bin/env python3
"""Run one check over the design history: did each claimed fix move the number?

  hvp_history.py --cmd "python3 tools/clearance.py --out results/clr.json" --out results/clr.json \
                 --metric min_clearance_mm [--paths cad/throttle params/throttle.json] [--grep OD-01]
                 [--revs A..B | --revs r1,r2,...] [--max 30] [--overlay tools/clearance.py]
                 [--claims "fix|수정|해결|resolve"] [--limit 0.2 --target 0.5 --sense min]
                 [--inputs data/x.step] [--timeout 1800] [--report results/history_od01.json]

The same check is run at every selected commit, oldest first, each in its own temporary worktree
(the working tree is never touched). Commit selection: --revs (a range or a list), otherwise the
last --max commits; --paths keeps only commits that touched those paths, --grep only commits
whose message matches.
--overlay PATH: copy today's version of PATH (the checker, a check definition) into every old
worktree, so today's check judges each old design. Without it each commit is judged by the checker
it had then, which mixes checker changes with design changes.

Report per commit: value, status, change from the previous run, and whether the commit message
claims a fix (--claims regex). Findings:
  CLAIM_NOT_SUPPORTED  a commit claims a fix but the metric did not improve (or the status did not)
  CLAIM_STILL_FAILING  a commit claims a fix and the check still does not pass there
  REGRESSED_AT         first commit where a passing status became non-passing
  NEVER_PASSED         no selected commit passes: the defect predates the range
  STUCK                the last N claimed fixes left the metric within --stuck-tol of each other
  NOT_RUN              the check could not run at that commit (missing file, error): see the log
This is a scenario over history (the data-platform idea of rerunning a new question over all
past runs): it answers "which change moved it, and did the fixes work", before the next attempt.
Exit codes: 0 the newest commit passes, 1 otherwise, 3 setup error.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
from hvp_sweep import find_metric  # noqa: E402


def commits(top, a):
    if a.revs and ".." not in a.revs:
        revs = [r.strip() for r in a.revs.split(",") if r.strip()]
        out = []
        for r in revs:
            line = C.git("log", "-1", "--format=%H%x09%ad%x09%s", "--date=short", r, cwd=top)
            out.append(line.split("\t", 2))
        return out
    args = ["log", "--reverse", "--format=%H%x09%ad%x09%s", "--date=short"]
    if a.grep:
        args += ["--grep", a.grep, "-i", "-E"]
    args.append(a.revs or "HEAD")
    if a.paths:
        args += ["--"] + a.paths
    lines = [x.split("\t", 2) for x in C.git(*args, cwd=top).split("\n") if x.strip()]
    return lines[-a.max:] if a.max else lines


def better(a, b, sense):
    """is value a better than b"""
    return a > b if sense == "min" else a < b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--metric", required=True)
    ap.add_argument("--revs")
    ap.add_argument("--paths", nargs="*", default=[])
    ap.add_argument("--grep")
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--overlay", nargs="*", default=[])
    ap.add_argument("--claims", default=r"fix|fixed|resolve|수정|해결|해소")
    ap.add_argument("--stuck", type=int, default=2, help="claimed fixes in a row that count as STUCK")
    ap.add_argument("--stuck-tol", type=float, default=0.0)
    ap.add_argument("--limit", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--sense", choices=["min", "max"])
    ap.add_argument("--inputs", nargs="*", default=[])
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--report")
    a = ap.parse_args()
    top = C.toplevel()
    cs = commits(top, a)
    if not cs:
        print("NO_COMMITS selected")
        return 3
    for p in a.overlay:
        if not os.path.exists(os.path.join(top, p)):
            print("INPUT_ERROR overlay not found: %s" % p)
            return 3
    claim_re = re.compile(a.claims, re.I)
    base = tempfile.mkdtemp(prefix="hvp-hist-")
    rows = []
    for i, (h, date, subj) in enumerate(cs):
        wt = C.candidate_worktree(top, base, "wt-%d" % i, h, with_changes=False, inputs=a.inputs)
        row = {"commit": h, "date": date, "subject": subj, "claim": bool(claim_re.search(subj))}
        try:
            for p in a.overlay:
                src, dst = os.path.join(top, p), os.path.join(wt, p)
                os.makedirs(os.path.dirname(dst) or wt, exist_ok=True)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
            log = os.path.join(base, "run-%d.log" % i)
            with open(log, "w") as lf:
                try:
                    rc = subprocess.run(["bash", "-c", a.cmd], cwd=wt, stdout=lf, stderr=subprocess.STDOUT,
                                        timeout=a.timeout, env=dict(os.environ, HVP_HISTORY_COMMIT=h)).returncode
                except subprocess.TimeoutExpired:
                    rc = "timeout"
            outp = os.path.join(wt, a.out)
            m = None
            if os.path.isfile(outp):
                try:
                    m = dict(find_metric(json.load(open(outp)), a.metric))
                except (KeyError, IndexError, ValueError):
                    m = None
            if m is None or not C._num(m.get("value")):
                tail = open(log, errors="replace").read()[-300:]
                row.update(status="NOT_RUN", value=None, rc=rc, log_tail=tail)
            else:
                for k in ("limit", "target", "sense"):
                    if getattr(a, k) is not None:
                        m[k] = getattr(a, k)
                row.update(value=m["value"], limit=m.get("limit"), target=m.get("target"),
                           sense=m.get("sense", "min"), status=C.metric_status(m) or "UNJUDGED", rc=rc)
        finally:
            C.git("worktree", "remove", "--force", wt, cwd=top, check=False)
        rows.append(row)

    sense = next((r["sense"] for r in rows if r.get("sense")), a.sense or "min")
    findings = []
    prev = None
    for r in rows:
        r["delta"] = None
        if r["value"] is not None and prev is not None:
            r["delta"] = r["value"] - prev["value"]
            if r["claim"]:
                improved = better(r["value"], prev["value"], sense) or \
                    C.RANK.get(r["status"], -1) > C.RANK.get(prev["status"], -1)
                if not improved:
                    findings.append("CLAIM_NOT_SUPPORTED %s %s (%s -> %s)" % (
                        r["commit"][:10], r["subject"][:60], "%.6g" % prev["value"], "%.6g" % r["value"]))
        if r["claim"] and r["value"] is not None and r["status"] not in C.PASSING:
            findings.append("CLAIM_STILL_FAILING %s %s (%s after the claimed fix)" % (
                r["commit"][:10], r["subject"][:60], r["status"]))
        if r["value"] is not None:
            prev = r
    ran = [r for r in rows if r["value"] is not None]
    for x, y in zip(ran, ran[1:]):
        if x["status"] in C.PASSING and y["status"] not in C.PASSING:
            findings.append("REGRESSED_AT %s %s (%s -> %s)" % (y["commit"][:10], y["subject"][:60], x["status"], y["status"]))
    if ran and not any(r["status"] in C.PASSING for r in ran):
        findings.append("NEVER_PASSED in %d evaluated commit(s): the defect predates %s" % (len(ran), ran[0]["commit"][:10]))
    claimed = [r for r in ran if r["claim"]]
    if len(claimed) >= a.stuck:
        last = [r["value"] for r in claimed[-a.stuck:]]
        before = [r for r in ran if ran.index(r) < ran.index(claimed[-a.stuck])]
        ref = before[-1]["value"] if before else last[0]
        if max(last + [ref]) - min(last + [ref]) <= a.stuck_tol:
            findings.append("STUCK the last %d claimed fixes left %s at %s" % (a.stuck, a.metric, "%.6g" % ref))
    nr = [r for r in rows if r["value"] is None]
    if nr:
        findings.append("NOT_RUN %d commit(s): %s" % (len(nr), ", ".join(r["commit"][:10] for r in nr)))

    print("CHECK    %s   metric %s (sense %s)   overlay %s" % (a.cmd, a.metric, sense, ",".join(a.overlay) or "none"))
    print("%-3s %-10s %-10s %-12s %-10s %-20s %s" % ("#", "commit", "date", "value", "delta", "status", "subject"))
    for i, r in enumerate(rows, 1):
        print("%-3d %-10s %-10s %-12s %-10s %-20s %s%s" % (
            i, r["commit"][:10], r["date"], "-" if r["value"] is None else "%.6g" % r["value"],
            "-" if r["delta"] is None else "%+.4g" % r["delta"], r["status"], "[claims fix] " if r["claim"] else "",
            r["subject"][:70]))
    for f in findings:
        print("FINDING  " + f)
    newest = rows[-1]
    print("NEWEST   %s %s" % (newest["commit"][:10], newest["status"]))
    if a.report:
        json.dump({"layer": "L2", "kind": "history scan (not a verdict)",
                   "scope": "check %s at %d commits" % (a.cmd, len(rows)),
                   "provenance": {"command": " ".join(sys.argv), "commit": C.head(top)},
                   "metric": a.metric, "sense": sense, "overlay": a.overlay, "rows": rows, "findings": findings},
                  open(os.path.join(top, a.report), "w"), indent=1)
    return 0 if newest["status"] in C.PASSING else 1


if __name__ == "__main__":
    sys.exit(main())

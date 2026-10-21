#!/usr/bin/env python3
"""Size a design change from data: run a cheap oracle over candidate parameter values.

  hvp_sweep.py --cmd "python3 src/build.py --gap {value} && python3 tools/clearance.py" \
               --out results/clearance.json --metric min_clearance_mm --values 0.2,0.3,0.4,0.6
               [--limit 0.2 --target 0.5 --sense min] [--commit HEAD] [--inputs data/x.step]
               [--timeout 1800] [--report /tmp/sweep.json] [--keep]

Each value runs in its own temporary worktree, so the working tree and canonical evidence are
never touched. By default the worktree is HEAD plus your uncommitted tracked changes and new
untracked files (the candidate you are evaluating); --commit REV uses that commit only.
{value} in --cmd is replaced by the value, which is also exported as HVP_SWEEP_VALUE.

--metric is a metric name under "metrics" (top level or in checks/cases/results/gates), or a
slash path into the JSON (/a/b/0/c). If the metric entry carries limit/target/sense they are
used unless given on the command line.

List --values in the order of increasing change (smallest edit first). The report gives the
status per value, whether the metric is monotonic over the list, the status transitions, and
the first value reaching the limit and the target. A sweep evaluates candidates; it is not a
verdict. Apply the chosen value and verify it through the normal chain (SKILL 7.8).
Exit codes: 0 some value reaches the target (or the limit when no target), 1 none does,
2 a run failed, 3 setup error.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def find_metric(doc, name):
    if name.startswith("/"):
        cur = doc
        for part in [p for p in name.split("/") if p]:
            if isinstance(cur, list):
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur[part]
            else:
                raise KeyError(name)
        return cur if isinstance(cur, dict) else {"value": cur}
    cands = []
    if isinstance(doc.get("metrics"), dict) and name in doc["metrics"]:
        cands.append(doc["metrics"][name])
    for lk in C.LIST_KEYS:
        for it in doc.get(lk, []) if isinstance(doc.get(lk), list) else []:
            if isinstance(it, dict) and isinstance(it.get("metrics"), dict) and name in it["metrics"]:
                cands.append(it["metrics"][name])
    if len(cands) != 1:
        raise KeyError("metric %s found %d times; use a /path" % (name, len(cands)))
    m = cands[0]
    return m if isinstance(m, dict) else {"value": m}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--metric", required=True)
    ap.add_argument("--values", required=True)
    ap.add_argument("--limit", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--sense", choices=["min", "max"])
    ap.add_argument("--commit", default=None, help="evaluate this commit only (default: HEAD + working changes)")
    ap.add_argument("--inputs", nargs="*", default=[])
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--report")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()
    values = [v.strip() for v in a.values.split(",") if v.strip()]
    if len(values) < 2:
        print("need at least 2 values")
        return 3
    for p in a.inputs:
        if not os.path.exists(os.path.join(top, p)):
            print("input not found:", p)
            return 3

    base = tempfile.mkdtemp(prefix="hvp-sweep-")
    rows, failed = [], []
    for i, v in enumerate(values):
        wt = C.candidate_worktree(top, base, "wt-%d" % i, a.commit or "HEAD", with_changes=a.commit is None,
                                  inputs=a.inputs)
        try:
            log = os.path.join(base, "run-%d.log" % i)
            with open(log, "w") as lf:
                try:
                    rc = subprocess.run(["bash", "-c", a.cmd.replace("{value}", v)], cwd=wt,
                                        env=dict(os.environ, HVP_SWEEP_VALUE=v), stdout=lf,
                                        stderr=subprocess.STDOUT, timeout=a.timeout).returncode
                except subprocess.TimeoutExpired:
                    rc = "timeout"
            outp = os.path.join(wt, a.out)
            if rc != 0 or not os.path.isfile(outp):
                failed.append(v)
                rows.append({"value": v, "metric": None, "status": "RUN_FAILED", "log": log})
                continue
            try:
                m = dict(find_metric(json.load(open(outp)), a.metric))
            except (KeyError, IndexError, ValueError) as e:
                failed.append(v)
                rows.append({"value": v, "metric": None, "status": "RUN_FAILED", "log": str(e)})
                continue
            for k in ("limit", "target", "sense"):
                if getattr(a, k) is not None:
                    m[k] = getattr(a, k)
            rows.append({"value": v, "metric": m.get("value"), "limit": m.get("limit"), "target": m.get("target"),
                         "sense": m.get("sense", "min"), "status": C.metric_status(m) or "UNJUDGED"})
        finally:
            C.git("worktree", "remove", "--force", wt, cwd=top, check=False)
    C.git("worktree", "prune", cwd=top, check=False)

    src = "commit %s" % a.commit if a.commit else "HEAD %s + working changes" % C.head(top)[:12]
    print("SWEEP %s over %s (%s)" % (a.metric, ",".join(values), src))
    print("CMD     each value ran: " + a.cmd.replace("{value}", "<value>") + "   (in a temporary worktree; read --out "
          + a.out + ")")
    print("  %-12s %-14s %s" % ("value", "metric", "status"))
    for r in rows:
        print("  %-12s %-14s %s" % (r["value"], r["metric"], r["status"]))
    good = [r for r in rows if C._num(r["metric"])]
    ms = [r["metric"] for r in good]
    if len(ms) >= 2:
        inc = all(x <= y for x, y in zip(ms, ms[1:]))
        dec = all(x >= y for x, y in zip(ms, ms[1:]))
        mono = "increasing" if inc else "decreasing" if dec else "no"
    else:
        mono = "unknown"
    print("MONOTONIC", mono)
    for x, y in zip(rows, rows[1:]):
        if x["status"] != y["status"]:
            print("TRANSITION between %s (%s) and %s (%s)" % (x["value"], x["status"], y["value"], y["status"]))
    first_pass = next((r["value"] for r in rows if C.RANK.get(r["status"], -1) >= C.RANK["PASS_BELOW_TARGET"]), None)
    has_target = any(r.get("target") is not None for r in good)
    first_target = next((r["value"] for r in rows if r["status"] == "PASS"), None)
    print("FIRST_LIMIT", first_pass or "none")
    print("FIRST_TARGET", (first_target or "none") if has_target else "n/a (no target)")
    if mono == "no":
        print("NOTE metric is not monotonic over these values: samples say nothing between them; "
              "refine around each TRANSITION before choosing")
    if failed:
        print("RESULT RUN_FAILED for %s (logs in %s)" % (",".join(failed), base))
    if a.report:
        with open(a.report, "w") as f:
            json.dump({"metric": a.metric, "source": src, "rows": rows, "monotonic": mono,
                       "first_limit": first_pass, "first_target": first_target}, f, indent=1)
    if a.keep or failed:
        print("work dir kept:", base)
    else:
        shutil.rmtree(base, ignore_errors=True)
    if failed:
        return 2
    reached = first_target if has_target else first_pass
    return 0 if reached else 1


if __name__ == "__main__":
    sys.exit(main())

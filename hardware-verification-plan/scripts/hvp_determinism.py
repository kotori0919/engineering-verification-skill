#!/usr/bin/env python3
"""Run one checker at the same commit under several order/seed settings and compare results.

  hvp_determinism.py --cmd "python3 tools/check.py" --out results/check.json
                     [--seeds 1,7,42,1234] [--axis PYTHONHASHSEED]
                     [--inputs cad/build/a.step data/x.csv] [--commit HEAD]
                     [--timeout 1800] [--ignore-keys a,b] [--grep tools/]

Each run uses its own temporary detached worktree, so canonical evidence is never touched.
A clean worktree lacks untracked inputs (built CAD, downloaded data): list them in --inputs
(paths relative to the repo root) and they are copied into each worktree. If you do not know
them, run once without --inputs and read the "missing" errors in the logs.

--axis sets the environment variable that carries each value (PYTHONHASHSEED by default;
OMP_NUM_THREADS or your own RNG seed variable for other axes).
Use >= 4 values for cheap checkers, >= 2 for expensive ones. Agreement is reported as
"n values agree", never as "deterministic": small sets can iterate identically under
different seeds.

--grep DIR also lists code that picks representatives from unordered iteration.
Exit codes: 0 all agree, 1 a value differs or a run failed, 3 setup error.
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

RISKY = re.compile(r"next\(iter\(|\bset\(|frozenset|listdir\(|glob\(|\.keys\(\)\)\[0\]|popitem\(")


def grep_risky(top, d):
    hits = []
    root = os.path.join(top, d)
    for r, _, fs in os.walk(root):
        for fn in fs:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(r, fn)
            try:
                for i, line in enumerate(open(p, errors="replace"), 1):
                    if RISKY.search(line):
                        hits.append("%s:%d: %s" % (os.path.relpath(p, top), i, line.strip()[:120]))
            except OSError:
                pass
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--out", required=True, help="evidence path written by the checker, repo-relative")
    ap.add_argument("--seeds", default="1,7,42,1234")
    ap.add_argument("--axis", default="PYTHONHASHSEED")
    ap.add_argument("--inputs", nargs="*", default=[])
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--ignore-keys", default="")
    ap.add_argument("--grep", default="")
    ap.add_argument("--keep", action="store_true", help="keep result directory")
    a = ap.parse_args()

    top = C.toplevel()
    commit = C.git("rev-parse", a.commit, cwd=top)
    seeds = [s for s in a.seeds.split(",") if s]
    if len(seeds) < 2:
        print("need at least 2 values")
        return 3
    for p in a.inputs:
        if not os.path.exists(os.path.join(top, p)):
            print("input not found:", p)
            return 3
    res_dir = tempfile.mkdtemp(prefix="hvp-det-")
    results, failed = {}, []
    for s in seeds:
        wt = os.path.join(res_dir, "wt-" + s)
        C.git("worktree", "add", "-q", "--detach", wt, commit, cwd=top)
        try:
            for p in a.inputs:
                src, dst = os.path.join(top, p), os.path.join(wt, p)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
            env = dict(os.environ, **{a.axis: s})
            log = os.path.join(res_dir, "run-%s.log" % s)
            with open(log, "w") as lf:
                try:
                    rc = subprocess.run(["bash", "-c", a.cmd], cwd=wt, env=env, stdout=lf, stderr=subprocess.STDOUT,
                                        timeout=a.timeout).returncode
                except subprocess.TimeoutExpired:
                    rc = "timeout"
            outp = os.path.join(wt, a.out)
            if rc == 0 and os.path.isfile(outp):
                shutil.copy2(outp, os.path.join(res_dir, "result-%s.json" % s))
                results[s] = json.load(open(outp))
                print("%s=%s exit 0" % (a.axis, s))
            else:
                failed.append(s)
                print("%s=%s exit %s, output %s (log %s)" % (a.axis, s, rc, "present" if os.path.isfile(outp) else "missing", log))
        finally:
            C.git("worktree", "remove", "--force", wt, cwd=top, check=False)
    print("commit", commit[:12], "| inputs copied:", " ".join(a.inputs) or "none")

    differs = False
    base = seeds[0]
    if base in results:
        for s in seeds[1:]:
            if s not in results:
                continue
            r = C.compare(results[base], results[s], [k for k in a.ignore_keys.split(",") if k])
            print("--- %s=%s vs %s=%s" % (a.axis, base, a.axis, s))
            C.print_compare(r, 20)
            differs |= bool(r["diff"])
    ok = [s for s in seeds if s in results]
    if failed:
        print("RESULT RUN_FAILED for %s; agreement not established" % ",".join(failed))
    elif differs:
        print("RESULT DIFFERENT: order-dependent checker; treat its verdicts as BLOCKED until fixed")
    else:
        print("RESULT %d values agree (%s=%s); this is not a proof of determinism" % (len(ok), a.axis, ",".join(ok)))
    if a.grep:
        hits = grep_risky(top, a.grep)
        print("--- unordered-iteration candidates in %s: %d" % (a.grep, len(hits)))
        for h in hits[:40]:
            print("   ", h)
    if a.keep:
        print("results kept in", res_dir)
    else:
        shutil.rmtree(res_dir, ignore_errors=True)
    C.git("worktree", "prune", cwd=top, check=False)
    return 1 if (failed or differs) else 0


if __name__ == "__main__":
    sys.exit(main())

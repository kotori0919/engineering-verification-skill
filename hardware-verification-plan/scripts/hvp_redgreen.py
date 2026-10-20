#!/usr/bin/env python3
"""Check that a regression test actually detects the defect it was written for.

  hvp_redgreen.py --test "python3 -m pytest tests/test_cam_clearance.py" --fix params/cam.json
                  [--base <rev before the fix>] [--expect "min_clearance"] [--inputs data/x.step]

GREEN: the test runs on HEAD + your working changes (+ new untracked files) and must pass.
RED:   the same tree with only the --fix paths put back to --base (default HEAD; use the
       issue's start commit when the fix is already committed) must FAIL.
       A path that did not exist at --base is removed for the red run.
--expect STRING: the red run's output must contain STRING, so a red caused by an unrelated
       error (import failure, missing file) is not mistaken for detection.

Both runs use temporary worktrees; the working tree is not touched.
Results: RED_GREEN_OK, GREEN_FAILS (the test fails even with the fix), TEST_DOES_NOT_DETECT
(passes without the fix), RED_FOR_OTHER_REASON (fails without the fix, but not with --expect).
Exit codes: 0 RED_GREEN_OK, 1 otherwise, 3 setup error.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def run(cmd, wt, log, timeout):
    with open(log, "w") as lf:
        try:
            rc = subprocess.run(["bash", "-c", cmd], cwd=wt, stdout=lf, stderr=subprocess.STDOUT,
                                timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    return rc, open(log, errors="replace").read()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", required=True)
    ap.add_argument("--fix", nargs="+", required=True, help="paths that make up the fix (repo-relative)")
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--expect", default="")
    ap.add_argument("--inputs", nargs="*", default=[])
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    top = C.toplevel()
    base = C.git("rev-parse", a.base, cwd=top)
    fix = [os.path.relpath(os.path.abspath(p), top) for p in a.fix]
    d = tempfile.mkdtemp(prefix="hvp-rg-")
    wts = []
    try:
        green = C.candidate_worktree(top, d, "green", "HEAD", with_changes=True, inputs=a.inputs)
        wts.append(green)
        red = C.candidate_worktree(top, d, "red", "HEAD", with_changes=True, inputs=a.inputs)
        wts.append(red)
        reverted = []
        for p in fix:
            old = subprocess.run(["git", "show", "%s:%s" % (base, p)], cwd=top, capture_output=True)
            dst = os.path.join(red, p)
            if old.returncode == 0:
                os.makedirs(os.path.dirname(dst) or red, exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(old.stdout)
                reverted.append(p)
            elif os.path.exists(dst):
                os.remove(dst)
                reverted.append(p + " (removed)")
        rg, gout = run(a.test, green, os.path.join(d, "green.log"), a.timeout)
        rr, rout = run(a.test, red, os.path.join(d, "red.log"), a.timeout)
    finally:
        for w in wts:
            C.git("worktree", "remove", "--force", w, cwd=top, check=False)
        C.git("worktree", "prune", cwd=top, check=False)

    print("base %s | fix paths reverted for RED: %s" % (base[:12], ", ".join(reverted) or "none"))
    print("GREEN (with fix)    exit %s" % rg)
    print("RED   (without fix) exit %s" % rr)
    if rg != 0:
        res = "GREEN_FAILS"
    elif rr == 0:
        res = "TEST_DOES_NOT_DETECT"
    elif a.expect and a.expect not in rout:
        res = "RED_FOR_OTHER_REASON"
    else:
        res = "RED_GREEN_OK"
    print("RESULT", res)
    if res != "RED_GREEN_OK":
        tail = (gout if res == "GREEN_FAILS" else rout).strip().splitlines()[-8:]
        for line in tail:
            print("   |", line)
    shutil.rmtree(d, ignore_errors=True)
    return 0 if res == "RED_GREEN_OK" else 1


if __name__ == "__main__":
    sys.exit(main())

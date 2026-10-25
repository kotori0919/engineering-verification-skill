#!/usr/bin/env python3
"""Pre-commit check of the staged set against this session's own changes.

  hvp_stagecheck.py --sid S [--max-mb 25] [--raw-pattern REGEX]

Run inside the session worktree (hvp_session.py get --sid S worktree). Checks:
  [1]  staged paths that are not in the session's touched list (someone else's files)
  [1'] touched paths that are not staged (confirm intent)
  [2]  staged blobs larger than --max-mb
  [3]  staged raw analysis output (meshes, result dumps, logs, packaged skills)
  [4]  commits that entered HEAD after start_head (all must be yours)
  [5]  HEAD is on a branch (not detached)
Commit only when [1] [2] [3] are empty, [4] lists only your commits and [5] is a branch.
Exit codes: 0 clean, 1 something to resolve.
"""
import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402

RAW = r"(^|/)raw/|_raw/|\.(vtu|vtk|frd|rmed|h5|hdf5|npz|npy|log|skill|mcap|bag|db3)$"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sid", required=True)
    ap.add_argument("--max-mb", type=float, default=25.0)
    ap.add_argument("--raw-pattern", default=RAW)
    a = ap.parse_args()
    lk = C.load_lock(a.sid)
    wt = lk["worktree"]
    if os.path.realpath(C.toplevel()) != os.path.realpath(wt):
        print("run this inside the session worktree:", wt)
        return 1
    own = set(lk.get("touched", []))
    staged = [p for p in C.git("diff", "--cached", "--name-only", cwd=wt).split("\n") if p]
    present = [p for p in C.git("diff", "--cached", "--name-only", "--diff-filter=d", cwd=wt).split("\n") if p]
    problems = 0

    foreign = sorted(set(staged) - own)
    print("[1] staged but not yours (unstage: git restore --staged -- <path>): %d" % len(foreign))
    for p in foreign:
        print("   ", p)
    problems += bool(foreign)

    missing = sorted(own - set(staged))
    print("[1'] yours but not staged (confirm intent): %d" % len(missing))
    for p in missing:
        print("   ", p)

    big = []
    for p in present:
        s = subprocess.run(["git", "cat-file", "-s", ":" + p], cwd=wt, capture_output=True, text=True).stdout.strip()
        if s.isdigit() and int(s) > a.max_mb * 1024 * 1024:
            big.append((int(s), p))
    print("[2] staged larger than %.0f MB: %d" % (a.max_mb, len(big)))
    for s, p in big:
        print("    %d %s" % (s, p))
    problems += bool(big)

    rx = re.compile(a.raw_pattern)
    raw = [p for p in present if rx.search(p)]
    print("[3] raw analysis output staged: %d" % len(raw))
    for p in raw:
        print("   ", p)
    problems += bool(raw)

    log = C.git("log", "--format=%h %an %s", "%s..HEAD" % lk["start_head"], cwd=wt, check=False)
    print("[4] commits since start_head (must all be yours):", "none" if not log else "")
    if log:
        print("    " + log.replace("\n", "\n    "))

    br = C.branch(cwd=wt)
    print("[5] HEAD:", br or "DETACHED (git switch <branch>)")
    problems += not br

    print("VERDICT:", "CLEAN" if not problems else "RESOLVE %d item(s) before committing" % problems)
    print("Stage only with: git add -- <path> ...   (never git add -A / git add .)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())

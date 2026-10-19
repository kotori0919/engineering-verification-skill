#!/usr/bin/env python3
"""Run a suite against exactly what would be committed (the index), in a clean worktree.

  hvp_snapshot.py --show-hook                       print the pre-push hook location and body
  hvp_snapshot.py --cmd "<suite command>"           build a commit object from the index,
                                                    check it out in a temp worktree, run the command
  hvp_snapshot.py --cmd "..." --parent <rev>        chain commit bundles in order

Untracked or ignored files that let the working tree pass are absent here, which is the point.
Refs do not move; the temporary commit object is left for git gc.
Run the command in the same environment the hook uses (e.g. "nix develop -c <suite>").
Exit code: the suite's exit code (0 = pass), 3 on setup error.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cmd")
    ap.add_argument("--parent", default="HEAD")
    ap.add_argument("--show-hook", action="store_true")
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args()
    top = C.toplevel()
    hd = C.hooks_dir(top)
    if a.show_hook:
        hook = os.path.join(hd, "pre-push")
        print("hooks dir:", hd)
        if os.path.isfile(hook):
            print("--- pre-push (first 80 lines)")
            with open(hook, errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= 80:
                        break
                    print(line.rstrip())
        else:
            print("no pre-push hook")
        if not a.cmd:
            return 0
    if not a.cmd:
        print("--cmd required")
        return 3
    tree = C.git("write-tree", cwd=top)
    commit = C.git("commit-tree", tree, "-p", a.parent, "-m", "hvp snapshot candidate", cwd=top)
    d = tempfile.mkdtemp(prefix="hvp-snap-")
    wt = os.path.join(d, "snap")
    C.git("worktree", "add", "-q", "--detach", wt, commit, cwd=top)
    print("snapshot commit", commit[:12], "parent", C.git("rev-parse", "--short", a.parent, cwd=top), "->", wt)
    try:
        env = dict(os.environ, HVP_SNAPSHOT_COMMIT=commit, HVP_HOOKS_DIR=hd)
        try:
            rc = subprocess.run(["bash", "-c", a.cmd], cwd=wt, env=env, timeout=a.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    finally:
        C.git("worktree", "remove", "--force", wt, cwd=top, check=False)
        shutil.rmtree(d, ignore_errors=True)
    print("SNAPSHOT_EXIT", rc, "(commit object %s; use it as --parent for the next bundle)" % commit[:12])
    return rc


if __name__ == "__main__":
    sys.exit(main())

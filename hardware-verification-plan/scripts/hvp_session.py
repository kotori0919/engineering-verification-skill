#!/usr/bin/env python3
"""Session lock, concurrency signals and worktree isolation.

  hvp_session.py start    --sid S            register; list other locks (ACTIVE/STALE)
  hvp_session.py signals  --sid S [--minutes 30]
                                             concurrency signals; exit 2 if concurrent
  hvp_session.py isolate  --sid S --commit REV
                                             dedicated worktree + branch hvp/S
  hvp_session.py touch    --sid S PATH...    record changed paths, refresh heartbeat
  hvp_session.py get      --sid S KEY        print a lock field (worktree, start_head, ...)
  hvp_session.py end      --sid S            remove a clean worktree, then the lock

The lock lives in <git-common-dir>/hvp-sessions/<S>.json: never committed, shared by all
worktrees. Agent tools open a new shell per command, so read values back with `get`.
Exit codes: 0 ok, 1 error/refused, 2 concurrent session detected.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def others(sid):
    now, rows = int(time.time()), []
    for f in sorted(glob.glob(os.path.join(C.lock_dir(), "*.json"))):
        if os.path.basename(f) == sid + ".json":
            continue
        try:
            d = json.load(open(f))
        except Exception:
            rows.append(("UNREADABLE", os.path.basename(f), None, ""))
            continue
        age = now - int(d.get("heartbeat", 0))
        rows.append(("ACTIVE" if age < C.ACTIVE_SECONDS else "STALE", d.get("session", f), age, d.get("worktree", "")))
    return rows


def cmd_start(a):
    os.makedirs(C.lock_dir(), exist_ok=True)
    rows = others(a.sid)
    for st, s, age, wt in rows:
        print("%-7s %s age=%ss %s" % (st, s, age, wt))
    top = C.toplevel()
    if os.path.exists(C.lock_path(a.sid)) and not a.force:
        print("lock already exists for %s; use --force to reset" % a.sid)
        return 1
    now = int(time.time())
    C.save_lock(a.sid, {"session": a.sid, "host": os.uname().nodename, "main_worktree": top,
                        "worktree": top, "branch": C.branch(), "start_head": C.head(),
                        "started": now, "touched": []})
    print("registered", C.lock_path(a.sid))
    active = [r for r in rows if r[0] == "ACTIVE"]
    if active:
        print("CONCURRENT: %d active lock(s). Isolation is mandatory (isolate)." % len(active))
        return 2
    return 0


def cmd_signals(a):
    lk = C.load_lock(a.sid)
    wt = lk["worktree"]
    concurrent = False
    act = [r for r in others(a.sid) if r[0] == "ACTIVE"]
    print("[locks] active others: %d" % len(act))
    for r in act:
        print("   ", r)
    concurrent |= bool(act)
    log = C.git("log", "--format=%h %an %ar %s", "%s..HEAD" % lk["start_head"], cwd=wt, check=False)
    print("[a] commits since start_head:", "none" if not log else "")
    if log:
        print("   " + log.replace("\n", "\n   "))
        concurrent = True
    touched = set(lk.get("touched", []))
    cutoff = time.time() - a.minutes * 60
    recent = []
    for root, dirs, files in os.walk(wt):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            p = os.path.join(root, fn)
            try:
                if os.path.getmtime(p) >= cutoff:
                    rel = os.path.relpath(p, wt)
                    if rel not in touched:
                        recent.append(rel)
            except OSError:
                pass
    print("[b] files modified in last %d min, not in touched: %d" % (a.minutes, len(recent)))
    for r in sorted(recent)[:20]:
        print("   ", r)
    procs, how = C.foreign_processes(wt)
    print("[c] other processes with cwd in worktree (%s): %d" % (how, len(procs)))
    for pid, c, cmd in procs:
        print("    %d %s" % (pid, cmd[:160]))
    if how == "unavailable":
        print("    (process listing unavailable: neither /proc nor lsof)")
    concurrent |= bool(procs)
    print("[b] is advisory: confirm recent files are not your own edits before calling them foreign.")
    print("VERDICT:", "CONCURRENT" if concurrent else "NONE (locks, commits, processes)")
    return 2 if concurrent else 0


def cmd_isolate(a):
    lk = C.load_lock(a.sid)
    base = lk["main_worktree"]
    wt = os.path.join(os.path.dirname(base), os.path.basename(base) + "-" + a.sid)
    br = "hvp/" + a.sid
    p = subprocess.run(["git", "-C", base, "worktree", "add", "-b", br, wt, a.commit], capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stderr.strip())
        return 1
    lk.update(worktree=wt, branch=br, start_head=C.head(cwd=wt))
    C.save_lock(a.sid, lk)
    print(wt)
    return 0


def cmd_touch(a):
    lk = C.load_lock(a.sid)
    wt = lk["worktree"]
    rel = []
    for p in a.paths:
        ap = os.path.abspath(p)
        rel.append(os.path.relpath(ap, wt) if ap.startswith(wt + os.sep) else p)
    lk["touched"] = sorted(set(lk.get("touched", [])) | set(rel))
    C.save_lock(a.sid, lk)
    print("touched: %d path(s)" % len(lk["touched"]))
    return 0


def cmd_get(a):
    v = C.load_lock(a.sid).get(a.key, "")
    print(json.dumps(v) if isinstance(v, (list, dict)) else v)
    return 0


def cmd_end(a):
    lk = C.load_lock(a.sid)
    lock_file = C.lock_path(a.sid)
    base, wt = lk["main_worktree"], lk["worktree"]
    os.chdir(base)                      # the worktree may be our cwd; leave it before removing it
    if wt != base:
        st = C.git("status", "--porcelain", cwd=wt, check=False)
        if st:
            print("worktree has changes; lock kept (stop and report): %s" % wt)
            print(st)
            return 1
        p = subprocess.run(["git", "-C", base, "worktree", "remove", wt], capture_output=True, text=True)
        if p.returncode != 0:
            print(p.stderr.strip())
            print("lock kept: %s" % wt)
            return 1
        print("worktree removed: %s (branch %s kept)" % (wt, lk.get("branch")))
    os.remove(lock_file)
    print("session ended")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--sid", required=True); s.add_argument("--force", action="store_true")
    s = sub.add_parser("signals"); s.add_argument("--sid", required=True); s.add_argument("--minutes", type=int, default=30)
    s = sub.add_parser("isolate"); s.add_argument("--sid", required=True); s.add_argument("--commit", default="HEAD")
    s = sub.add_parser("touch"); s.add_argument("--sid", required=True); s.add_argument("paths", nargs="+")
    s = sub.add_parser("get"); s.add_argument("--sid", required=True); s.add_argument("key")
    s = sub.add_parser("end"); s.add_argument("--sid", required=True)
    a = ap.parse_args()
    return {"start": cmd_start, "signals": cmd_signals, "isolate": cmd_isolate,
            "touch": cmd_touch, "get": cmd_get, "end": cmd_end}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

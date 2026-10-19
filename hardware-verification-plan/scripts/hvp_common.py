"""Shared helpers for the hardware-verification-plan scripts.

Standard library only. Works on Linux and macOS with Python 3.8+.
Every script is run from inside the target git repository (any worktree).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time

ACTIVE_SECONDS = 2 * 3600

# ---------------------------------------------------------------- git

def git(*args, cwd=None, check=True, text=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=text)
    if check and p.returncode != 0:
        raise SystemExit("git %s failed: %s" % (" ".join(args), (p.stderr or "").strip()))
    return p.stdout.strip() if text else p.stdout


def toplevel(cwd=None):
    return git("rev-parse", "--show-toplevel", cwd=cwd)


def common_dir(cwd=None):
    return git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)


def head(cwd=None):
    return git("rev-parse", "HEAD", cwd=cwd)


def branch(cwd=None):
    p = subprocess.run(["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=cwd,
                       capture_output=True, text=True)
    return p.stdout.strip()


def hooks_dir(cwd=None):
    top = toplevel(cwd)
    p = subprocess.run(["git", "config", "core.hooksPath"], cwd=cwd, capture_output=True, text=True)
    hp = p.stdout.strip()
    if not hp:
        return git("rev-parse", "--path-format=absolute", "--git-path", "hooks", cwd=cwd)
    return hp if os.path.isabs(hp) else os.path.join(top, hp)

# ---------------------------------------------------------------- session lock

def lock_dir(cwd=None):
    return os.path.join(common_dir(cwd), "hvp-sessions")


def lock_path(sid, cwd=None):
    return os.path.join(lock_dir(cwd), sid + ".json")


def load_lock(sid, cwd=None):
    p = lock_path(sid, cwd)
    if not os.path.exists(p):
        raise SystemExit("session lock not found: %s (run hvp_session.py start first)" % p)
    with open(p) as f:
        return json.load(f)


def save_lock(sid, data, cwd=None):
    os.makedirs(lock_dir(cwd), exist_ok=True)
    data["heartbeat"] = int(time.time())
    p = lock_path(sid, cwd)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, p)

# ---------------------------------------------------------------- hashing

_cache = {}


def sha256_file(path):
    key = os.path.abspath(path)
    if key not in _cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _cache[key] = h.hexdigest()
    return _cache[key]


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()

# ---------------------------------------------------------------- semantic compare

HEX = re.compile(r"^[0-9a-f]{40,64}$")
# Ignored only when the value looks like a hash: "head" may be a real bolt-head type.
ID_NAMES = {"sha256", "hash", "digest", "commit", "design_commit", "git_commit", "head", "head_commit"}
# Ignored by exact key name only. Never by substring: "head"/"duration" appear in physical quantities.
VOLATILE_NAMES = {"timestamp", "generated_at", "created_at", "started_at", "finished_at", "elapsed",
                  "elapsed_s", "wall_time_s", "runtime_s", "hostname", "host", "tmpdir", "tmp_dir",
                  "cwd", "pid"}
ID_SUFFIXES = ("_sha256", "_hash", "_digest")


def flatten(x, p=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from flatten(v, p + "/" + str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from flatten(v, "%s[%d]" % (p, i))
    else:
        yield p, x


def segments(p):
    return [re.sub(r"(\[\d+\])+$", "", s) for s in p.split("/") if s]


def _hexlike(v):
    return v is None or (isinstance(v, str) and bool(HEX.match(v)))


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def compare(a, b, extra_ignore=(), rel=1e-9, abs_=1e-12):
    """Return dict(diff=[(path, a, b)], ignored=set(keys), hex_only=[paths])."""
    names = VOLATILE_NAMES | set(extra_ignore)
    fa, fb = dict(flatten(a)), dict(flatten(b))
    diff, hex_only, ignored = [], [], set()

    def same(x, y):
        if _num(x) and _num(y):
            if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
                return True
            return math.isclose(x, y, rel_tol=rel, abs_tol=abs_)
        return x == y

    for k in sorted(set(fa) | set(fb)):
        x, y = fa.get(k), fb.get(k)
        s = segments(k)
        last = s[-1] if s else ""
        vol = bool(s) and ("volatile" in s or last in names or
                           ((last in ID_NAMES or last.endswith(ID_SUFFIXES)) and _hexlike(x) and _hexlike(y)))
        if vol:
            if not same(x, y):
                ignored.add(last)
            continue
        if same(x, y):
            continue
        if isinstance(x, str) and isinstance(y, str) and HEX.match(x) and HEX.match(y):
            hex_only.append(k)
        else:
            diff.append((k, x, y))
    return {"diff": diff, "ignored": ignored, "hex_only": hex_only}


def print_compare(res, limit=50, out=sys.stdout):
    d = res["diff"]
    print("EQUIVALENT" if not d else "DIFFERENT %d" % len(d), file=out)
    for k, x, y in d[:limit]:
        print("  %s %r -> %r" % (k, x, y), file=out)
    print("ignored volatile keys (changed): %s" % (", ".join(sorted(res["ignored"])) or "none"), file=out)
    if res["hex_only"]:
        print("hash-like values changed in %d field(s), check: %s" % (len(res["hex_only"]), ", ".join(res["hex_only"][:10])), file=out)


def load_json_spec(spec, cwd=None):
    """'path.json' or 'git:<rev>:<path>'."""
    if spec.startswith("git:"):
        _, rev, path = spec.split(":", 2)
        return json.loads(git("show", "%s:%s" % (rev, path), cwd=cwd))
    with open(spec) as f:
        return json.load(f)

# ---------------------------------------------------------------- processes


def ppid_of(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except Exception:
        p = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
        try:
            return int(p.stdout.strip())
        except ValueError:
            return 0


def cmdline_of(pid):
    p = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return p.stdout.strip()


def processes_with_cwd_under(root):
    """[(pid, cwd)] for processes whose cwd is root or below. Uses /proc, else lsof."""
    root = os.path.realpath(root)
    found = []
    if os.path.isdir("/proc") and os.path.exists("/proc/self/cwd"):
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                c = os.path.realpath(os.readlink("/proc/%s/cwd" % d))
            except OSError:
                continue
            if c == root or c.startswith(root + os.sep):
                found.append((int(d), c))
        return found, "proc"
    p = subprocess.run(["lsof", "-a", "-d", "cwd", "-F", "pn"], capture_output=True, text=True)
    if p.returncode not in (0, 1):
        return [], "unavailable"
    pid = None
    for line in p.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            c = os.path.realpath(line[1:])
            if c == root or c.startswith(root + os.sep):
                found.append((pid, c))
    return found, "lsof"


def foreign_processes(root):
    """Processes under root that are not this command: excludes our ancestors, our children,
    and siblings under the same invoking shell (pipeline partners such as `| head`).
    Other children of the agent harness (our own background jobs) are reported; the caller
    recognises them from the command line."""
    me = os.getpid()
    parent = ppid_of(me)
    anc, p = set(), me
    while p > 1:
        anc.add(p)
        p = ppid_of(p)
    found, how = processes_with_cwd_under(root)
    out = []
    for pid, c in found:
        if pid in anc:
            continue
        pp = ppid_of(pid)
        if pp == me or (parent > 1 and pp == parent):
            continue
        out.append((pid, c, cmdline_of(pid)))
    return out, how

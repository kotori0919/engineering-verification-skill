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

# ---------------------------------------------------------------- verdict records
#
# Evidence convention (oracle-catalog 29):
#   {"status": "PASS", "layer": "L2", "question": "assembly",
#    "metrics": {"min_clearance_mm": {"value": 0.35, "limit": 0.2, "target": 0.5, "sense": "min"}}}
# or several checks in one file under "checks" / "cases" / "results" / "gates":
#   {"checks": [{"id": "gear_mesh", "status": "FAIL", "metrics": {...}}]}
# sense "min": value must be >= limit (bigger is better); "max": value must be <= limit.

# INDETERMINATE: passes the limit only by less than its own uncertainty (cannot be told from FAIL)
RANK = {"FAIL": 0, "INDETERMINATE": 1, "PASS_BY_MODEL_CHANGE": 2, "PASS_BELOW_TARGET": 3, "PASS": 4}
PASSING = ("PASS", "PASS_BELOW_TARGET", "PASS_BY_MODEL_CHANGE")
LIST_KEYS = ("checks", "cases", "results", "gates")


def metric_status(m):
    """Status implied by one metric entry, or None if it cannot be judged."""
    if not isinstance(m, dict) or not _num(m.get("value")) or not _num(m.get("limit")):
        return None
    v, lim, tgt = m["value"], m["limit"], m.get("target")
    u = abs(m["uncertainty"]) if _num(m.get("uncertainty")) else 0.0
    lo = m.get("sense", "min") != "max"
    if (v < lim) if lo else (v > lim):
        return "FAIL"
    # the value must clear the limit (and the target) by more than its own uncertainty
    if (v - u < lim) if lo else (v + u > lim):
        return "INDETERMINATE"
    if _num(tgt) and ((v - u < tgt) if lo else (v + u > tgt)):
        return "PASS_BELOW_TARGET"
    return "PASS"


def margin(m):
    if not isinstance(m, dict) or not _num(m.get("value")) or not _num(m.get("limit")):
        return None
    return (m["value"] - m["limit"]) if m.get("sense", "min") != "max" else (m["limit"] - m["value"])


def derived_status(metrics):
    st = [metric_status(m) for m in (metrics or {}).values()]
    st = [s for s in st if s]
    if not st:
        return None
    return min(st, key=lambda s: RANK[s])


def _record(d):
    metrics = d.get("metrics") if isinstance(d.get("metrics"), dict) else {}
    status = d.get("status") if isinstance(d.get("status"), str) else None
    return {"status": status, "metrics": metrics, "layer": d.get("layer"), "question": d.get("question")}


def verdict_records(doc, path):
    """{check_id: record} for one evidence document. Files without status/metrics give {}."""
    out = {}
    if not isinstance(doc, dict):
        return out
    if isinstance(doc.get("status"), str) or isinstance(doc.get("metrics"), dict):
        out[path] = _record(doc)
    for lk in LIST_KEYS:
        items = doc.get(lk)
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            cid = it.get("id", it.get("name"))
            if cid is None or not (isinstance(it.get("status"), str) or isinstance(it.get("metrics"), dict)):
                continue
            r = _record(it)
            if r["layer"] is None:
                r["layer"] = doc.get("layer")
            out["%s#%s" % (path, cid)] = r
    return out


def effective_status(rec):
    s = rec.get("status")
    return s if s else derived_status(rec.get("metrics"))


def json_files(spec, top, pattern=""):
    """{path: doc} for *.json in the worktree ('worktree'), at a revision ('git:<rev>'), or under a
    directory snapshot ('dir:<path>', paths relative to it, ignored files included)."""
    import fnmatch
    docs = {}
    if spec.startswith("dir:"):
        base = os.path.abspath(spec[4:])
        for r, ds, fs in os.walk(base):
            ds[:] = sorted(d for d in ds if not d.startswith("."))
            for fn in sorted(fs):
                if not fn.endswith(".json"):
                    continue
                rel = os.path.relpath(os.path.join(r, fn), base)
                if pattern and not fnmatch.fnmatch(rel, pattern):
                    continue
                try:
                    with open(os.path.join(r, fn)) as fh:
                        docs[rel] = json.load(fh)
                except Exception:
                    continue
        return docs
    if spec in ("", "worktree"):
        names = [f for f in git("ls-files", "-co", "--exclude-standard", "*.json", cwd=top).split("\n") if f]
        for f in names:
            if pattern and not fnmatch.fnmatch(f, pattern):
                continue
            try:
                with open(os.path.join(top, f)) as fh:
                    docs[f] = json.load(fh)
            except Exception:
                continue
        return docs
    if not spec.startswith("git:"):
        raise SystemExit("spec must be 'worktree', 'git:<rev>' or 'dir:<path>': %s" % spec)
    rev = spec[4:]
    names = [f for f in git("ls-tree", "-r", "--name-only", rev, cwd=top).split("\n") if f.endswith(".json")]
    for f in names:
        if pattern and not fnmatch.fnmatch(f, pattern):
            continue
        try:
            docs[f] = json.loads(git("show", "%s:%s" % (rev, f), cwd=top))
        except Exception:
            continue
    return docs

# ---------------------------------------------------------------- candidate worktrees


def candidate_worktree(top, base_dir, name, commit="HEAD", with_changes=True, inputs=()):
    """Detached worktree at commit, plus (optionally) the uncommitted tracked changes of `top`
    and copies of listed untracked inputs. Returns its path. Caller removes it."""
    import shutil
    wt = os.path.join(base_dir, name)
    git("worktree", "add", "-q", "--detach", wt, git("rev-parse", commit, cwd=top), cwd=top)
    if with_changes:
        patch = git("diff", "--binary", "HEAD", cwd=top, text=False)
        if patch.strip():
            p = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=wt, input=patch,
                               capture_output=True)
            if p.returncode != 0:
                git("worktree", "remove", "--force", wt, cwd=top, check=False)
                raise SystemExit("could not apply working changes: %s" % p.stderr.decode(errors="replace"))
        # new files the candidate added (untracked, not ignored) travel with it
        inputs = list(inputs) + [f for f in git("ls-files", "-o", "--exclude-standard", cwd=top).split("\n")
                                 if f and f not in inputs]
    for p in inputs:
        src, dst = os.path.join(top, p), os.path.join(wt, p)
        os.makedirs(os.path.dirname(dst) or wt, exist_ok=True)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
    return wt

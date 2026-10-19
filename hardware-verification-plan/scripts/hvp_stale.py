#!/usr/bin/env python3
"""Find evidence files whose recorded input hashes no longer match the inputs.

  hvp_stale.py                       scan every tracked/untracked *.json evidence
  hvp_stale.py --file SRC            which evidence records SRC (by path or by its HEAD hash)
  hvp_stale.py --glob 'results/**/*.json'   limit the scan
  hvp_stale.py --json                machine-readable output

Recognised record shapes (rename keys with --path-key/--hash-key if your repo differs):
  {"<path>": "<sha256>"}            {"<path>": {"sha256": "<sha256>"}}
  {"path": "<path>", "sha256": "<sha256>"}    {"x_path": "<path>", "x_sha256": "<sha256>"}

Output lines: STALE <evidence> -> <input>, MISSING <evidence> -> <input>.
The ORDER section lists stale evidence so that evidence recording other stale evidence
comes later (regenerate in that order, then run this again until STALE is 0).
Exit codes: 0 nothing stale, 1 stale or missing found.
Limits: dependencies that are read but never hashed in evidence are not visible here.
"""
import argparse
import fnmatch
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402

H64 = re.compile(r"^[0-9a-f]{64}$")


def is_path(s):
    return isinstance(s, str) and ("/" in s or "." in s) and len(s) < 1024 and not H64.match(s)


def pairs(n, path_key="path", hash_key="sha256"):
    if isinstance(n, dict):
        for k, v in n.items():
            if isinstance(v, str) and H64.match(v) and is_path(k):
                yield k, v
            if isinstance(v, dict) and isinstance(v.get(hash_key), str) and H64.match(v[hash_key]) and is_path(k):
                yield k, v[hash_key]
        for pk in [k for k in n if k == path_key or k.endswith("_" + path_key)]:
            hk = hash_key if pk == path_key else pk[: -len(path_key)] + hash_key
            pv, hv = n.get(pk), n.get(hk)
            if is_path(pv) and isinstance(hv, str) and H64.match(hv):
                yield pv, hv
        for v in n.values():
            yield from pairs(v, path_key, hash_key)
    elif isinstance(n, list):
        for v in n:
            yield from pairs(v, path_key, hash_key)


def resolve(p, ev, top):
    for cand in (p, os.path.join(top, p), os.path.join(os.path.dirname(os.path.join(top, ev)), p)):
        if os.path.isfile(cand):
            return cand
    return None


def evidence_files(top, pattern):
    out = C.git("ls-files", "-co", "--exclude-standard", "*.json", cwd=top).split("\n")
    out = [f for f in out if f]
    if pattern:
        out = [f for f in out if fnmatch.fnmatch(f, pattern)]
    return out


def scan(top, pattern, pk, hk):
    stale, missing = [], []
    for ev in evidence_files(top, pattern):
        try:
            d = json.load(open(os.path.join(top, ev)))
        except Exception:
            continue
        seen = set()
        for p, h in pairs(d, pk, hk):
            if (p, h) in seen:
                continue
            seen.add((p, h))
            rp = resolve(p, ev, top)
            if rp is None:
                missing.append((ev, p))
            elif C.sha256_file(rp) != h:
                stale.append((ev, os.path.relpath(rp, top)))
    return stale, missing


def order(stale):
    evs = sorted({e for e, _ in stale})
    deps = {e: {i for x, i in stale if x == e and i in evs} for e in evs}
    done, out = set(), []
    while len(out) < len(evs):
        ready = [e for e in evs if e not in done and deps[e] <= done]
        if not ready:                      # cycle: emit the rest as-is
            out += [e for e in evs if e not in done]
            break
        for e in ready:
            done.add(e)
            out.append(e)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="report evidence that records this source path or its HEAD hash")
    ap.add_argument("--glob", default="", help="fnmatch pattern for evidence paths")
    ap.add_argument("--path-key", default="path")
    ap.add_argument("--hash-key", default="sha256")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()

    if a.file:
        rel = os.path.relpath(os.path.abspath(a.file), top)
        old = C.git("show", "HEAD:%s" % rel, cwd=top, check=False, text=False)
        old_h = C.sha256_bytes(old) if old else None
        new_h = C.sha256_file(os.path.join(top, rel)) if os.path.isfile(os.path.join(top, rel)) else None
        hits = []
        for ev in evidence_files(top, a.glob):
            try:
                txt = open(os.path.join(top, ev)).read()
            except Exception:
                continue
            if rel in txt or (old_h and old_h in txt):
                hits.append(ev)
        res = {"file": rel, "head_sha256": old_h, "worktree_sha256": new_h, "changed": old_h != new_h, "recorded_by": hits}
        if a.json:
            print(json.dumps(res, indent=1))
        else:
            print("file %s changed=%s head=%s now=%s" % (rel, res["changed"], (old_h or "-")[:12], (new_h or "-")[:12]))
            for h in hits:
                print("RECORDED_BY", h)
            if not hits:
                print("RECORDED_BY none")
        return 0

    stale, missing = scan(top, a.glob, a.path_key, a.hash_key)
    if a.json:
        print(json.dumps({"stale": stale, "missing": missing, "order": order(stale)}, indent=1))
    else:
        for e, p in stale:
            print("STALE  ", e, "->", p)
        for e, p in missing:
            print("MISSING", e, "->", p)
        if stale:
            print("ORDER (regenerate top to bottom, then rerun this scan):")
            for e in order(stale):
                print("   ", e)
        print("SUMMARY stale=%d missing=%d" % (len(stale), len(missing)))
    return 1 if (stale or missing) else 0


if __name__ == "__main__":
    sys.exit(main())

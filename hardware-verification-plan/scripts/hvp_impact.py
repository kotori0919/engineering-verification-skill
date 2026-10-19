#!/usr/bin/env python3
"""Before a change: which evidence (and so which checks) depends on the paths you will touch.

  hvp_impact.py src/build_cam.py params/cam.json   evidence depending on these paths
  hvp_impact.py --diff [REV]                       paths changed vs REV (default HEAD), including
                                                   uncommitted and untracked files
  hvp_impact.py --glob 'verification/**/*.json' --json

Dependencies come from input hashes recorded in evidence (same record shapes as hvp_stale.py),
followed transitively: evidence that records impacted evidence is impacted too.
A path that appears in an evidence file's text but not as a hashed input is listed as
MENTIONED: a weaker link, check it by hand.

Output
  IMPACTED <layer> <evidence>  [via <path or evidence>]   rerun in the listed order
  MENTIONED <evidence> -> <path>
  UNTRACKED_DEPENDENCY <path>   no evidence records this path; hashing cannot tell you what
                                depends on it, so rerun the whole cheap tier (L0-L2)
Order: dependencies first; among evidence that is ready, the lowest layer (L0..L5, unknown last).
Exit codes: 0 always on success (this is a planning aid), 3 on input error.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
import hvp_stale as S  # noqa: E402


def layer_key(layer):
    m = re.match(r"^L(\d)", str(layer or ""))
    return int(m.group(1)) if m else 9


def changed_paths(top, rev):
    out = set(f for f in C.git("diff", "--name-only", rev, cwd=top).split("\n") if f)
    out |= set(f for f in C.git("ls-files", "-o", "--exclude-standard", cwd=top).split("\n") if f)
    return sorted(out)


def build_graph(top, pattern, pk, hk):
    deps, text, layer = {}, {}, {}
    for ev in S.evidence_files(top, pattern):
        try:
            raw = open(os.path.join(top, ev)).read()
            d = json.loads(raw)
        except Exception:
            continue
        ins = set()
        for p, _h in S.pairs(d, pk, hk):
            rp = S.resolve(p, ev, top)
            ins.add(os.path.relpath(rp, top) if rp else os.path.normpath(p))
        deps[ev] = ins
        text[ev] = raw
        layer[ev] = d.get("layer") if isinstance(d, dict) else None
    return deps, text, layer


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--diff", nargs="?", const="HEAD", default=None, metavar="REV")
    ap.add_argument("--glob", default="")
    ap.add_argument("--path-key", default="path")
    ap.add_argument("--hash-key", default="sha256")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()
    paths = [os.path.relpath(os.path.abspath(p), top) for p in a.paths]
    if a.diff:
        paths += changed_paths(top, a.diff)
    paths = sorted(set(paths))
    if not paths:
        print("INPUT_ERROR no paths (give paths or --diff)")
        return 3

    deps, text, layer = build_graph(top, a.glob, a.path_key, a.hash_key)
    impacted, via = set(), {}
    frontier = set(paths)
    while frontier:
        nxt = set()
        for ev, ins in deps.items():
            if ev in impacted:
                continue
            hit = sorted(ins & frontier)
            if hit:
                impacted.add(ev)
                via[ev] = hit[0]
                nxt.add(ev)
        frontier = nxt
    impacted -= set(paths)            # an edited evidence file is input, not a rerun target

    # order: layer, then dependencies first
    done, order = set(), []
    pending = sorted(impacted, key=lambda e: (layer_key(layer.get(e)), e))
    while pending:
        ready = [e for e in pending if not (deps[e] & impacted) - done]
        pick = ready[0] if ready else pending[0]
        order.append(pick)
        done.add(pick)
        pending.remove(pick)          # lowest layer among those whose dependencies are done

    recorded = set().union(*deps.values()) if deps else set()
    untracked = [p for p in paths if p not in recorded and p not in deps]
    mentioned = sorted((ev, p) for ev in deps if ev not in impacted for p in paths
                       if p not in deps[ev] and p in text[ev])

    if a.json:
        print(json.dumps({"paths": paths, "impacted": [{"evidence": e, "layer": layer.get(e), "via": via[e]}
                                                       for e in order],
                          "mentioned": mentioned, "untracked_dependency": untracked}, indent=1))
        return 0
    print("CHANGED", " ".join(paths))
    for e in order:
        print("IMPACTED %-3s %s  [via %s]" % (layer.get(e) or "-", e, via[e]))
    for e, p in mentioned:
        print("MENTIONED", e, "->", p)
    for p in untracked:
        print("UNTRACKED_DEPENDENCY", p)
    print("SUMMARY impacted=%d mentioned=%d untracked_dependency=%d" % (len(order), len(mentioned), len(untracked)))
    if untracked:
        print("NOTE no hashed record for %d path(s): rerun the whole cheap tier (L0-L2) after the change" % len(untracked))
    return 0


if __name__ == "__main__":
    sys.exit(main())

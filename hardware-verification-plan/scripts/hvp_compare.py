#!/usr/bin/env python3
"""Semantic comparison of two JSON evidence files.

  hvp_compare.py BEFORE AFTER [--ignore-keys a,b] [--rel 1e-9] [--abs 1e-12]

BEFORE/AFTER: a file path, or git:<rev>:<path> (e.g. git:HEAD:results/x.json).

Rules
- Volatile keys are dropped only on an exact key-name match (timestamps, elapsed_s, hostname,
  tmp dirs, anything under a key named "volatile"). Substrings never match, so
  bolt_head_diameter_mm or fault_duration_s are always compared.
- ID keys (commit, head, sha256, *_sha256, ...) are dropped only when both values look like
  hashes; a "head" holding a real value is compared.
- Numbers compare with math.isclose(rel, abs); the absolute floor keeps near-zero residuals
  (1e-13 vs 3e-13) from reading as change.
- Ignored keys that changed and hash-only differences are printed for a human check.

Exit codes: 0 EQUIVALENT, 1 DIFFERENT, 3 input error.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--ignore-keys", default="", help="extra exact key names to treat as volatile")
    ap.add_argument("--rel", type=float, default=1e-9)
    ap.add_argument("--abs", type=float, default=1e-12)
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    try:
        x = C.load_json_spec(a.before)
        y = C.load_json_spec(a.after)
    except Exception as e:  # noqa: BLE001
        print("INPUT_ERROR", e)
        return 3
    res = C.compare(x, y, [k for k in a.ignore_keys.split(",") if k], a.rel, a.abs)
    C.print_compare(res, a.limit)
    return 1 if res["diff"] else 0


if __name__ == "__main__":
    sys.exit(main())

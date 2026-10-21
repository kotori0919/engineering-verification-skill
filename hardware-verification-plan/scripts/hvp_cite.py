#!/usr/bin/env python3
"""Make every reported number checkable by the person who owns the project.

  hvp_cite.py get results/wall.json /metrics/safety_factor/value
        print the value with where it came from: file, pointer, file sha256, the commit that
        last changed the file, whether the file has uncommitted changes, its recorded inputs
        (OK / STALE / MISSING), the recorded generating command, and how to look at it yourself

  hvp_cite.py check report.md [--allow-uncited]
        verify a report: every citation `ev:<file>#<pointer>` must resolve, and the number (or
        word) written just before it on the same line must equal the evidence value at the
        precision shown. Lines outside code blocks that state a number with an engineering
        unit but carry no citation are listed as UNCITED.

Citation form in reports (works in markdown and plain text):
    housing safety factor 1.32 `ev:results/wall.json#/metrics/safety_factor/value`
A citation to a status:  wall FAIL `ev:results/wall.json#/status`

Exit codes: get 0 found, 1 not found. check 0 clean, 1 mismatch / missing / uncited, 3 input error.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
import hvp_stale as S  # noqa: E402

CITE = re.compile(r"`?ev:([^#\s`]+)#(/[^\s`]*)`?")
NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
UNITS = r"(?:mm|cm|µm|um|m|N·m|Nm|N|kN|MPa|GPa|Pa|kPa|bar|°C|K|W|kW|V|A|mA|Ω|kg|g|rpm|Hz|%|°|mm³|mm²|SF)"
UNIT_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?\s?" + UNITS + r"(?![\w])")
SF_WORDS = re.compile(r"(안전율|safety factor|여유|clearance|간격)\s*[:=]?\s*[-+]?\d", re.I)


def pointer(doc, ptr):
    cur = doc
    for part in [p for p in ptr.split("/") if p != ""]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(ptr)
    return cur


def provenance(top, rel):
    full = os.path.join(top, rel)
    out = {"file": rel, "sha256": C.sha256_file(full)}
    log = subprocess.run(["git", "log", "-1", "--format=%H %cs %s", "--", rel], cwd=top,
                         capture_output=True, text=True).stdout.strip()
    out["last_commit"] = log or "untracked (never committed)"
    st = subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=top, capture_output=True,
                        text=True).stdout.strip()
    out["uncommitted_changes"] = bool(st)
    doc = json.load(open(full))
    ins = []
    for p, h in S.pairs(doc):
        rp = S.resolve(p, rel, top)
        state = "MISSING" if rp is None else ("OK" if C.sha256_file(rp) == h else "STALE")
        ins.append((p, state))
    out["inputs"] = ins
    pv = doc.get("provenance") if isinstance(doc, dict) else None
    out["command"] = (pv or {}).get("command") if isinstance(pv, dict) else None
    out["generated_at_commit"] = (pv or {}).get("commit") if isinstance(pv, dict) else None
    return out, doc


def cmd_get(a, top):
    rel = os.path.relpath(os.path.abspath(a.file), top)
    if not os.path.isfile(os.path.join(top, rel)):
        print("NOT_FOUND file", rel)
        return 1
    pv, doc = provenance(top, rel)
    try:
        val = pointer(doc, a.pointer or "")
    except (KeyError, IndexError, ValueError):
        print("NOT_FOUND %s#%s" % (rel, a.pointer))
        return 1
    print("VALUE    %s" % json.dumps(val, ensure_ascii=False)[:400])
    print("CITE     `ev:%s#%s`" % (rel, a.pointer or "/"))
    print("FILE     %s  sha256 %s%s" % (rel, pv["sha256"][:16], "  (uncommitted changes)" if pv["uncommitted_changes"] else ""))
    print("COMMIT   %s" % pv["last_commit"])
    if pv["generated_at_commit"]:
        print("GEN_AT   %s" % pv["generated_at_commit"])
    print("COMMAND  %s" % (pv["command"] or "not recorded in the evidence (ask for it or find it in the run table)"))
    if pv["inputs"]:
        for p, s in pv["inputs"]:
            print("INPUT    %-7s %s" % (s, p))
        if any(s != "OK" for _, s in pv["inputs"]):
            print("WARNING  some recorded inputs changed since this evidence was written: the value may not describe the current design")
    else:
        print("INPUT    none recorded: which design this value describes cannot be checked from the file")
    print("SEE      open %s, or: python3 -m json.tool %s" % (rel, rel))
    if pv["command"]:
        print("REPRO    %s   then rerun this command and compare" % pv["command"])
    return 0


def same(shown, actual):
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return str(actual) == shown
    try:
        x = float(shown)
    except ValueError:
        return False
    dec = len(shown.split(".")[1]) if "." in shown and "e" not in shown.lower() else None
    if dec is not None:
        return round(actual, dec) == round(x, dec) or abs(actual - x) <= 0.5 * 10 ** (-dec) + 1e-12
    return abs(actual - x) <= max(1e-9, 1e-6 * abs(actual))


def cmd_check(a, top):
    try:
        lines = open(a.report, encoding="utf-8").read().splitlines()
    except OSError as e:
        print("INPUT_ERROR", e)
        return 3
    ok = bad = 0
    uncited = []
    in_code = False
    cache = {}
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        cites = list(CITE.finditer(line))
        for m in cites:
            rel, ptr = m.group(1), m.group(2)
            if rel not in cache:
                full = os.path.join(top, rel)
                try:
                    cache[rel] = json.load(open(full))
                except Exception:
                    cache[rel] = None
            if cache[rel] is None:
                print("MISSING  line %d: %s (file not found or not JSON)" % (i, rel))
                bad += 1
                continue
            try:
                val = pointer(cache[rel], ptr)
            except (KeyError, IndexError, ValueError):
                print("MISSING  line %d: %s#%s (pointer not in file)" % (i, rel, ptr))
                bad += 1
                continue
            # text this citation vouches for: back to the previous citation, but citations that
            # stand next to each other ("-0.15 mm FAIL `ev:..value` `ev:..status`") share the text
            k = cites.index(m)
            start = m.start()
            while k > 0 and not line[cites[k - 1].end():start].strip():
                k -= 1
                start = cites[k].start()
            prev_end = cites[k - 1].end() if k > 0 else 0
            seg = line[prev_end:start]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                nums = NUM.findall(seg)
                shown = nums[-1] if nums else None
                if shown is None:
                    print("NO_VALUE line %d: citation %s#%s has no number before it" % (i, rel, ptr))
                    bad += 1
                elif same(shown, val):
                    ok += 1
                else:
                    print("MISMATCH line %d: report says %s, %s#%s is %r" % (i, shown, rel, ptr, val))
                    bad += 1
            else:
                if str(val) in seg:
                    ok += 1
                else:
                    print("MISMATCH line %d: %s#%s is %r, not stated before the citation" % (i, rel, ptr, val))
                    bad += 1
        if not cites and (UNIT_NUM.search(line) or SF_WORDS.search(line)):
            uncited.append((i, line.strip()))
    for i, t in uncited:
        print("UNCITED  line %d: %s" % (i, t[:140]))
    print("SUMMARY citations_ok=%d problems=%d uncited=%d" % (ok, bad, len(uncited)))
    if uncited and not a.allow_uncited:
        print("NOTE     numbers without a source cannot be checked by the reader: cite them, or mark them as "
              "estimates (INFERRED/PROPOSED) with how they were obtained")
    return 1 if bad or (uncited and not a.allow_uncited) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="op", required=True)
    g = sub.add_parser("get")
    g.add_argument("file")
    g.add_argument("pointer", nargs="?", default="")
    c = sub.add_parser("check")
    c.add_argument("report")
    c.add_argument("--allow-uncited", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()
    return cmd_get(a, top) if a.op == "get" else cmd_check(a, top)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Make every reported number checkable by the person who owns the project.

  hvp_cite.py get results/wall.json /metrics/safety_factor/value [--rerun]
        what the value means (scope, assumptions, limitations), where it came from (file hash,
        last commit, uncommitted changes, recorded inputs OK/STALE/MISSING, generating command),
        and how to look at it yourself. --rerun runs the recorded command in a temporary
        worktree and compares the regenerated value with the file (REPRODUCED / NOT_REPRODUCED).

  hvp_cite.py check report.md [--rerun] [--allow-uncited] [--allow-no-context]
        verify a report:
          MISMATCH       no number written before the citation equals the evidence value at the
                         precision shown (or the cited word is not there)
          MISSING        file or pointer does not exist
          NO_CONTEXT     cited JSON evidence has no scope/assumptions/limitations: the reader
                         cannot tell what the number measures
          UNCITED        a number with a unit, or a margin/ratio/factor number, with no citation
          UNCITED_JUDGMENT  "sufficient", "no problem", "충분", "문제없음" ... with no citation
          ESTIMATE       a number or judgment with no citation but labelled INFERRED / PROPOSED /
                         추정 / (제안): listed so the reader sees every estimate; not a failure
          NOT_REPRODUCED (--rerun) the recorded command regenerates a different value

Citation forms
  inline     safety factor 1.32 `ev:results/wall.json#/metrics/safety_factor/value`
  status     wall FAIL `ev:results/wall.json#/status`
  text line  M3 x 10 mm, 4 pcs `ev:bom.csv#L4`      (a number written must appear in that line)
  footnote   safety factor 1.32[^sf]   ...   [^sf]: ev:results/wall.json#/metrics/safety_factor/value
Citations standing next to each other share the text before them ("-0.15 mm FAIL `ev:..` `ev:..`").
Code blocks and headings are not checked.

Exit codes: get 0 found, 1 not found / not reproduced. check 0 clean, 1 problems, 3 input error.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
import hvp_stale as S  # noqa: E402

CITE = re.compile(r"`?ev:([^#\s`]+)#(/[^\s`]*|L\d+)`?")
FOOT_DEF = re.compile(r"^\s*\[\^([^\]]+)\]:\s*`?ev:([^#\s`]+)#(/[^\s`]*|L\d+)`?")
FOOT_USE = re.compile(r"\[\^([^\]]+)\](?!:)")
NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
UNITS = r"(?:mm|cm|µm|um|m|N·m|Nm|N|kN|MPa|GPa|Pa|kPa|bar|°C|K|W|kW|V|A|mA|Ω|kg|g|rpm|Hz|%|°|mm³|mm²|SF|L/min|m/s)"
UNIT_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?\s?" + UNITS + r"(?![\w])")
RATIO_NUM = re.compile(r"(안전율|안전계수|여유|비율|계수|효율|확률|손상|수명|safety factor|margin|ratio|factor|"
                       r"efficiency|probability|damage|life)\s*[:=은는이가]?\s*[-+]?\d", re.I)
JUDGE = re.compile(r"(충분|문제\s?없|괜찮|안전하|여유\s?있|만족|이상\s?없|sufficient|adequate|no problem|"
                   r"\bsafe\b|\bfine\b|looks good|\bOK\b)", re.I)
CONTEXT_KEYS = ("scope", "assumptions", "limitations")
ESTIMATE = re.compile(r"(INFERRED|PROPOSED|추정|\(제안\))")


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


def context_for(doc, ptr):
    """Nearest scope/assumptions/limitations along the pointer path (item level beats file level)."""
    found, cur = {}, doc
    chain = [cur]
    for part in [p for p in ptr.split("/") if p != ""]:
        try:
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        except (KeyError, IndexError, ValueError, TypeError):
            break
        chain.append(cur)
    for node in chain:
        if isinstance(node, dict):
            for k in CONTEXT_KEYS:
                if k in node:
                    found[k] = node[k]
    return found


def load(top, rel, cache):
    if rel not in cache:
        full = os.path.join(top, rel)
        if not os.path.isfile(full):
            cache[rel] = ("missing", None)
        elif rel.endswith(".json"):
            try:
                cache[rel] = ("json", json.load(open(full)))
            except Exception:
                cache[rel] = ("bad", None)
        else:
            cache[rel] = ("text", open(full, errors="replace").read().splitlines())
    return cache[rel]


def provenance(top, rel, doc):
    full = os.path.join(top, rel)
    out = {"file": rel, "sha256": C.sha256_file(full)}
    out["last_commit"] = subprocess.run(["git", "log", "-1", "--format=%H %cs %s", "--", rel], cwd=top,
                                        capture_output=True, text=True).stdout.strip() or "untracked (never committed)"
    out["uncommitted_changes"] = bool(subprocess.run(["git", "status", "--porcelain", "--", rel], cwd=top,
                                                     capture_output=True, text=True).stdout.strip())
    ins = []
    for p, h in S.pairs(doc):
        rp = S.resolve(p, rel, top)
        ins.append((p, "MISSING" if rp is None else ("OK" if C.sha256_file(rp) == h else "STALE")))
    out["inputs"] = ins
    pv = doc.get("provenance") if isinstance(doc, dict) and isinstance(doc.get("provenance"), dict) else {}
    out["command"] = pv.get("command")
    out["generated_at_commit"] = pv.get("commit")
    return out


def rerun(top, rel, ptrs, command, inputs, timeout=1800):
    """Run the recorded command in a temporary worktree; return {ptr: (status, new_value)}."""
    base = tempfile.mkdtemp(prefix="hvp-cite-")
    res = {}
    try:
        wt = C.candidate_worktree(top, base, "rerun", "HEAD", with_changes=True, inputs=inputs)
        try:
            with open(os.path.join(base, "rerun.log"), "w") as lf:
                rc = subprocess.run(["bash", "-c", command], cwd=wt, stdout=lf, stderr=subprocess.STDOUT,
                                    timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        newf = os.path.join(wt, rel)
        new = json.load(open(newf)) if os.path.isfile(newf) else None
        cur = json.load(open(os.path.join(top, rel)))
        for p in ptrs:
            if new is None:
                res[p] = ("RERUN_FAILED exit %s" % rc, None)
                continue
            try:
                a, b = pointer(cur, p), pointer(new, p)
            except (KeyError, IndexError, ValueError):
                res[p] = ("RERUN_FAILED pointer missing after rerun", None)
                continue
            same = (C._num(a) and C._num(b) and abs(a - b) <= max(1e-9, 1e-9 * abs(a))) or a == b
            res[p] = ("REPRODUCED" if same else "NOT_REPRODUCED", b)
        C.git("worktree", "remove", "--force", wt, cwd=top, check=False)
    finally:
        C.git("worktree", "prune", cwd=top, check=False)
        shutil.rmtree(base, ignore_errors=True)
    return res


def shown_matches(shown, actual):
    try:
        x = float(shown)
    except ValueError:
        return False
    if "." in shown and "e" not in shown.lower():
        dec = len(shown.split(".")[1])
        return abs(actual - x) <= 0.5 * 10 ** (-dec) + 1e-12
    return abs(actual - x) <= max(1e-9, 1e-6 * abs(actual))


def cmd_get(a, top):
    rel = os.path.relpath(os.path.abspath(a.file), top)
    kind, doc = load(top, rel, {})
    if kind != "json":
        print("NOT_FOUND file %s (%s)" % (rel, kind))
        return 1
    try:
        val = pointer(doc, a.pointer or "")
    except (KeyError, IndexError, ValueError):
        print("NOT_FOUND %s#%s" % (rel, a.pointer))
        return 1
    ctx = context_for(doc, a.pointer or "")
    pv = provenance(top, rel, doc)
    print("VALUE    %s" % json.dumps(val, ensure_ascii=False)[:400])
    parent = None
    try:
        parent = pointer(doc, (a.pointer or "").rsplit("/", 1)[0]) if (a.pointer or "").count("/") > 1 else None
    except (KeyError, IndexError, ValueError):
        pass
    if isinstance(parent, dict) and "limit" in parent:
        print("CRITERIA limit %s, target %s, sense %s%s" % (parent.get("limit"), parent.get("target"),
              parent.get("sense", "min"), ", uncertainty %s" % parent["uncertainty"] if "uncertainty" in parent else ""))
    for k in CONTEXT_KEYS:
        print("%-8s %s" % (k.upper()[:8], json.dumps(ctx[k], ensure_ascii=False) if k in ctx else "not recorded"))
    if not ctx:
        print("WARNING  no scope/assumptions/limitations: what this number measures is not in the evidence")
    cs = doc.get("criteria_source") if isinstance(doc, dict) else None
    if cs:
        print("CRIT_SRC %s" % json.dumps(cs, ensure_ascii=False))
    print("CITE     `ev:%s#%s`" % (rel, a.pointer or "/"))
    print("FILE     %s  sha256 %s%s" % (rel, pv["sha256"][:16], "  (uncommitted changes)" if pv["uncommitted_changes"] else ""))
    print("COMMIT   %s" % pv["last_commit"])
    print("COMMAND  %s" % (pv["command"] or "not recorded in the evidence"))
    for p, s in pv["inputs"]:
        print("INPUT    %-7s %s" % (s, p))
    if not pv["inputs"]:
        print("INPUT    none recorded: which design this value describes cannot be checked from the file")
    elif any(s != "OK" for _, s in pv["inputs"]):
        print("WARNING  recorded inputs changed since this evidence was written")
    print("SEE      open %s, or: python3 -m json.tool %s" % (rel, rel))
    rc = 0
    if a.rerun:
        if not pv["command"]:
            print("RERUN    NO_COMMAND: cannot reproduce, the evidence does not record how it was made")
            rc = 1
        else:
            st, nv = rerun(top, rel, [a.pointer or ""], pv["command"], a.inputs)[a.pointer or ""]
            print("RERUN    %s (file %s, regenerated %s)" % (st, json.dumps(val)[:60], json.dumps(nv)[:60]))
            rc = 0 if st == "REPRODUCED" else 1
    elif pv["command"]:
        print("REPRO    %s   (or: hvp_cite.py get ... --rerun)" % pv["command"])
    return rc


def cmd_check(a, top):
    try:
        lines = open(a.report, encoding="utf-8").read().splitlines()
    except OSError as e:
        print("INPUT_ERROR", e)
        return 3
    foot = {}
    for line in lines:
        m = FOOT_DEF.match(line)
        if m:
            foot[m.group(1)] = (m.group(2), m.group(3))
    cache, problems, warn, ok = {}, [], [], 0
    rerun_want = {}
    in_code = False
    for i, line in enumerate(lines, 1):
        st = line.strip()
        if st.startswith("```"):
            in_code = not in_code
            continue
        if in_code or st.startswith("#") or FOOT_DEF.match(line):
            continue
        cites = [(m.start(), m.end(), m.group(1), m.group(2)) for m in CITE.finditer(line)]
        for m in FOOT_USE.finditer(line):
            if m.group(1) in foot:
                rel, ptr = foot[m.group(1)]
                cites.append((m.start(), m.end(), rel, ptr))
            else:
                problems.append("MISSING  line %d: footnote [^%s] has no ev: definition" % (i, m.group(1)))
        cites.sort()
        for k, (s0, e0, rel, ptr) in enumerate(cites):
            j, start = k, s0
            while j > 0 and not line[cites[j - 1][1]:start].strip():
                j -= 1
                start = cites[j][0]
            seg = line[(cites[j - 1][1] if j > 0 else 0):start]
            kind, doc = load(top, rel, cache)
            if kind in ("missing", "bad"):
                problems.append("MISSING  line %d: %s (%s)" % (i, rel, "file not found" if kind == "missing" else "not valid JSON"))
                continue
            if ptr.startswith("L"):
                n = int(ptr[1:])
                src = doc if kind == "text" else open(os.path.join(top, rel), errors="replace").read().splitlines()
                if not 1 <= n <= len(src):
                    problems.append("MISSING  line %d: %s#%s (no such line)" % (i, rel, ptr))
                    continue
                nums = NUM.findall(seg)
                srcnums = set(NUM.findall(src[n - 1]))
                if nums and not any(x in srcnums for x in nums):
                    problems.append("MISMATCH line %d: none of %s appears in %s line %d: %r" % (i, nums, rel, n, src[n - 1][:80]))
                else:
                    ok += 1
                continue
            if kind != "json":
                problems.append("MISSING  line %d: %s#%s (JSON pointer into a non-JSON file; use #L<n>)" % (i, rel, ptr))
                continue
            try:
                val = pointer(doc, ptr)
            except (KeyError, IndexError, ValueError):
                problems.append("MISSING  line %d: %s#%s (pointer not in file)" % (i, rel, ptr))
                continue
            if C._num(val):
                nums = NUM.findall(seg)
                if not nums:
                    problems.append("NO_VALUE line %d: %s#%s has no number before it" % (i, rel, ptr))
                    continue
                if not any(shown_matches(x, val) for x in nums):
                    problems.append("MISMATCH line %d: report says %s, %s#%s is %r" % (i, "/".join(nums), rel, ptr, val))
                    continue
                is_evidence = bool(C.verdict_records(doc, rel))   # design inputs (params) are not evidence
                if is_evidence and not context_for(doc, ptr) and not a.allow_no_context:
                    warn.append("NO_CONTEXT line %d: %s has no scope/assumptions/limitations" % (i, rel))
                ok += 1
                if is_evidence:
                    rerun_want.setdefault(rel, set()).add(ptr)
            else:
                if str(val) in seg:
                    ok += 1
                else:
                    problems.append("MISMATCH line %d: %s#%s is %r, not stated before the citation" % (i, rel, ptr, val))
        if not cites:
            if (UNIT_NUM.search(line) or RATIO_NUM.search(line) or JUDGE.search(line)) and ESTIMATE.search(line):
                warn.append("ESTIMATE line %d: %s" % (i, st[:140]))
            elif UNIT_NUM.search(line) or RATIO_NUM.search(line):
                warn.append("UNCITED  line %d: %s" % (i, st[:140]))
            elif JUDGE.search(line):
                warn.append("UNCITED_JUDGMENT line %d: %s" % (i, st[:140]))
    reruns = []
    if a.rerun:
        for rel, ptrs in sorted(rerun_want.items()):
            doc = cache[rel][1]
            cmd = (doc.get("provenance") or {}).get("command") if isinstance(doc, dict) else None
            if not cmd:
                problems.append("NO_COMMAND %s: cannot reproduce, no provenance.command" % rel)
                continue
            for p, (stt, nv) in sorted(rerun(top, rel, sorted(ptrs), cmd, a.inputs).items()):
                line = "%s %s#%s -> %s" % (stt, rel, p, json.dumps(nv))
                reruns.append(line)
                if stt != "REPRODUCED":
                    problems.append(line)
    for x in problems:
        print(x)
    for x in warn:
        print(x)
    for x in reruns:
        if x.startswith("REPRODUCED"):
            print(x)
    uncited = [w for w in warn if w.startswith("UNCITED")]
    estimates = [w for w in warn if w.startswith("ESTIMATE")]
    noctx = [w for w in warn if w.startswith("NO_CONTEXT")]
    print("SUMMARY citations_ok=%d problems=%d uncited=%d no_context=%d estimates=%d%s" % (
        ok, len(problems), len(uncited), len(noctx), len(estimates),
        " reproduced=%d/%d" % (sum(r.startswith("REPRODUCED") for r in reruns), len(reruns)) if a.rerun else ""))
    if uncited and not a.allow_uncited:
        print("NOTE     cite each number to its evidence or source line (ev:<file>#L<n> for specs, BOMs),"
              " or label it INFERRED/PROPOSED with how it was obtained")
    fail = bool(problems) or (uncited and not a.allow_uncited) or (noctx and not a.allow_no_context)
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="op", required=True)
    g = sub.add_parser("get")
    g.add_argument("file")
    g.add_argument("pointer", nargs="?", default="")
    g.add_argument("--rerun", action="store_true")
    g.add_argument("--inputs", nargs="*", default=[])
    c = sub.add_parser("check")
    c.add_argument("report")
    c.add_argument("--rerun", action="store_true")
    c.add_argument("--inputs", nargs="*", default=[])
    c.add_argument("--allow-uncited", action="store_true")
    c.add_argument("--allow-no-context", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()
    return cmd_get(a, top) if a.op == "get" else cmd_check(a, top)


if __name__ == "__main__":
    sys.exit(main())

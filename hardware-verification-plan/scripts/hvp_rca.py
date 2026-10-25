#!/usr/bin/env python3
"""Root-cause ledger for a defect that survives fixes: what is ruled out, what is not confirmed,
what to test next. A file in the repository, so it outlives sessions and machines.

  hvp_rca.py new    verification/rca/OD-01.json --issue OD-01 --symptom "<one line>"
  hvp_rca.py check  verification/rca/OD-01.json          problems listed, exit 1 if any
  hvp_rca.py report verification/rca/OD-01.json [--md]   the state, for the user to judge

File (JSON):
  {"issue": "OD-01", "symptom": "throttle stays at 0 mm when -5 mm is requested with contacts on",
   "symptom_evidence": ["ev:results/fusion_stroke.json#/metrics/progress_mm/value"],
   "hypotheses": [
     {"id": "H1", "claim": "a contact pair blocks the first millimetres",
      "test": "hvp_isolate.py over the contact pairs", "cost": "20 runs x 30 s",
      "if_true": "all-off moves, a minimal pair set is found",
      "if_false": "all-off still stalls (FAILS_WITHOUT_ITEMS)",
      "status": "untested | confirmed | ruled_out | not_confirmed",
      "evidence": ["ev:results/isolate_od01.json#/result"], "note": ""}],
   "attempts": [{"ref": "<commit or fixloop issue#attempt>", "hypothesis": "H1", "result": "<one line>"}]}

Status meanings (each needs the test that decided it):
  confirmed      the test's if_true outcome was observed, and a fix based on it behaved as predicted
  ruled_out      the if_false outcome was observed
  not_confirmed  the test ran but did not decide (insufficient data, solver noise, sampling)
  untested       not yet tested
check reports
  NO_TEST          a hypothesis without test, if_true or if_false (it cannot be decided)
  NO_EVIDENCE      confirmed / ruled_out / not_confirmed without an ev: citation
  BROKEN_CITATION  a cited file or pointer does not resolve
  FIX_WITHOUT_CAUSE an attempt rests on a hypothesis that is not confirmed (or names none):
                   the usual reason a defect survives several fixes
  FIX_ON_RULED_OUT an attempt rests on a ruled-out hypothesis
                   Both are problems for an attempt without a "result" (planned or running). For an
                   attempt with a result they are listed as PAST: the record of why it came back.
  ALL_RULED_OUT    every hypothesis is ruled out: widen the search (new hypotheses, the controls
                   of hvp_isolate.py, the history scan of hvp_history.py)
Exit codes: 0 no problems, 1 problems found, 3 input error.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
from hvp_cite import pointer  # noqa: E402

STATUSES = ("confirmed", "ruled_out", "not_confirmed", "untested")
EV = re.compile(r"^ev:([^#\s]+)#(/\S*|L\d+)$")


def resolve(top, cite):
    m = EV.match(cite.strip())
    if not m:
        return "not an ev:<file>#<pointer> citation"
    path, ptr = m.groups()
    full = os.path.join(top, path)
    if not os.path.isfile(full):
        return "file not found"
    if ptr.startswith("L"):
        n = int(ptr[1:])
        return None if 1 <= n <= sum(1 for _ in open(full, errors="replace")) else "line out of range"
    try:
        pointer(json.load(open(full)), ptr)
    except Exception as e:  # noqa: BLE001
        return "pointer does not resolve (%s)" % e
    return None


def check(doc, top):
    probs = []
    hyps = {h.get("id"): h for h in doc.get("hypotheses", [])}
    for cite in doc.get("symptom_evidence", []):
        err = resolve(top, cite)
        if err:
            probs.append("BROKEN_CITATION symptom %s: %s" % (cite, err))
    for hid, h in hyps.items():
        st = h.get("status", "untested")
        if st not in STATUSES:
            probs.append("BAD_STATUS %s: %s (use %s)" % (hid, st, ", ".join(STATUSES)))
        if not (h.get("test") and h.get("if_true") and h.get("if_false")):
            probs.append("NO_TEST %s: needs test, if_true and if_false to be decidable" % hid)
        if st in ("confirmed", "ruled_out", "not_confirmed") and not h.get("evidence"):
            probs.append("NO_EVIDENCE %s is %s without an ev: citation" % (hid, st))
        for cite in h.get("evidence", []):
            err = resolve(top, cite)
            if err:
                probs.append("BROKEN_CITATION %s %s: %s" % (hid, cite, err))
    past = []
    for i, at in enumerate(doc.get("attempts", []), 1):
        h = hyps.get(at.get("hypothesis"))
        ref = at.get("ref", "#%d" % i)
        if h is None:
            msg = "FIX_WITHOUT_CAUSE attempt %s names no known hypothesis" % ref
        elif h.get("status") == "ruled_out":
            msg = "FIX_ON_RULED_OUT attempt %s rests on %s, which is ruled out" % (ref, h["id"])
        elif h.get("status") != "confirmed":
            msg = "FIX_WITHOUT_CAUSE attempt %s rests on %s, which is %s" % (ref, h["id"], h.get("status", "untested"))
        else:
            continue
        # an attempt with a recorded result is history (why the defect came back), not a defect of the ledger;
        # an attempt without a result is planned or running, and must not go ahead
        (past if at.get("result") else probs).append(msg)
    if hyps and all(h.get("status") == "ruled_out" for h in hyps.values()):
        probs.append("ALL_RULED_OUT widen the search: new hypotheses, isolation controls, history scan")
    return probs, past


def report(doc, md):
    hyps = doc.get("hypotheses", [])
    groups = [(s, [h for h in hyps if h.get("status", "untested") == s]) for s in STATUSES]
    names = {"confirmed": "확인됨 (confirmed)", "ruled_out": "배제됨 (ruled out)",
             "not_confirmed": "확인 안 됨 (not confirmed)", "untested": "아직 시험 안 함 (untested): 다음 시험 후보"}
    out = []
    out.append("### 원인 분석: %s" % doc.get("issue", "?") if md else "ISSUE    %s" % doc.get("issue", "?"))
    out.append(("증상: %s" if md else "SYMPTOM  %s") % doc.get("symptom", ""))
    for cite in doc.get("symptom_evidence", []):
        out.append(("- 증상 근거 `%s`" if md else "  evidence %s") % cite)
    for s, hs in groups:
        if not hs:
            continue
        out.append("")
        if md:
            out.append("#### %s" % names[s])
            out.append("| id | 가설 | 시험 | 맞다면 | 아니라면 | 비용 | 근거 |")
            out.append("| --- | --- | --- | --- | --- | --- | --- |")
            for h in hs:
                out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                    h.get("id"), h.get("claim", ""), h.get("test", ""), h.get("if_true", ""), h.get("if_false", ""),
                    h.get("cost", "-"), "<br>".join("`%s`" % c for c in h.get("evidence", [])) or "-"))
        else:
            out.append("%s" % s.upper())
            for h in hs:
                out.append("  %-4s %s" % (h.get("id"), h.get("claim", "")))
                out.append("       test %s | if true: %s | if false: %s | cost %s" % (
                    h.get("test", "-"), h.get("if_true", "-"), h.get("if_false", "-"), h.get("cost", "-")))
                for c in h.get("evidence", []):
                    out.append("       evidence %s" % c)
    if doc.get("attempts"):
        out.append("")
        if md:
            out.append("#### 수정 시도")
            out.append("| ref | 근거 가설 | 결과 |")
            out.append("| --- | --- | --- |")
            for at in doc["attempts"]:
                out.append("| %s | %s | %s |" % (at.get("ref"), at.get("hypothesis", "-"), at.get("result", "")))
        else:
            out.append("ATTEMPTS")
            for at in doc["attempts"]:
                out.append("  %s  on %s: %s" % (at.get("ref"), at.get("hypothesis", "-"), at.get("result", "")))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["new", "check", "report"])
    ap.add_argument("file")
    ap.add_argument("--issue")
    ap.add_argument("--symptom", default="")
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args()
    top = C.toplevel()
    path = os.path.join(top, a.file) if not os.path.isabs(a.file) else a.file
    if a.op == "new":
        if os.path.exists(path):
            print("EXISTS %s (edit it; a ledger is never reset)" % a.file)
            return 3
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        json.dump({"issue": a.issue or os.path.splitext(os.path.basename(path))[0], "symptom": a.symptom,
                   "symptom_evidence": [], "hypotheses": [], "attempts": []}, open(path, "w"), indent=1, ensure_ascii=False)
        print("NEW %s" % a.file)
        return 0
    try:
        doc = json.load(open(path))
    except (OSError, ValueError) as e:
        print("INPUT_ERROR %s: %s" % (a.file, e))
        return 3
    if a.op == "report":
        print(report(doc, a.md))
    probs, past = check(doc, top)
    if a.op == "check" or probs or past:
        for p in past:
            print(("- 지난 시도: %s" if a.md else "PAST     %s") % p)
        for p in probs:
            print(("- **%s**" if a.md else "PROBLEM  %s") % p)
        if not probs:
            print("OK       %d hypotheses, %d attempts, no problems%s" % (
                len(doc.get("hypotheses", [])), len(doc.get("attempts", [])),
                " (%d past attempt(s) without a confirmed cause, listed above)" % len(past) if past else ""))
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Decision brief: everything the project owner needs to judge verification and design, built
straight from the evidence files (no agent-written numbers).

  hvp_brief.py [--out-dir docs/verification] [--glob 'results/**/*.json'] [--decisions verification/decisions.json]
               [--title "cam-housing"]

Writes two files (default docs/verification/):
  brief.html  self-contained page to read (open it in a browser; no network, light/dark)
  brief.md    the same content with an `ev:<file>#<pointer>` citation on every number, so
              `hvp_cite.py check brief.md --rerun` can verify the brief itself

Sections
  1 header        repository, HEAD, uncommitted paths, evidence fingerprint, how it was generated
  2 attention     what to look at first: FAIL, BLOCKED, INCONSISTENT, INDETERMINATE, model-change
                  passes, below-target passes, stale or missing inputs, missing context /
                  uncertainty / generating command
  3 decisions     open decisions from the decisions file: options, evidence, cost, reversibility,
                  recommendation, what is blocked
  4 coverage      part x question (physical / assembly / motion / common): worst status per cell,
                  empty cells shown as "no check"
  5 checks        one card per check: status, every metric (value, limit, target, uncertainty,
                  margin), what it measures (scope, assumptions, limitations), criteria source,
                  method, inputs and their state, generating command, last commit, sensitivity
  6 design inputs each parameter file: its current values and which checks depend on it
  7 independent   agreement of independent methods (hvp_consensus.py evidence)
  8 limits        what this brief cannot tell you
Exit codes: 0 written, 3 input error. The brief is informational; verdicts stay in the evidence.
"""
import argparse
import datetime
import hashlib
import html
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402
import hvp_stale as S  # noqa: E402

SEVERITY = ["FAIL", "BLOCKED", "INCONSISTENT", "INDETERMINATE", "PASS_BY_MODEL_CHANGE", "PASS_BELOW_TARGET",
            "NOT_RUN", "UNKNOWN", "PASS"]
QUESTIONS = [("physical", "물리적 타당성"), ("assembly", "조립 가능성"), ("motion", "의도 동작"), ("common", "공통")]
QALIAS = {"physical": "physical", "타당성": "physical", "assembly": "assembly", "조립": "assembly",
          "motion": "motion", "동작": "motion", "common": "common", "공통": "common"}


def fmt(v):
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, (int, float)):
        return "%.6g" % v
    return str(v)


STATE_WORD = {"OK": "해시 일치", "STALE": "해시 불일치(STALE: 증거 작성 뒤 바뀜)", "MISSING": "파일 없음(MISSING)"}


def esc(x):
    return html.escape(str(x), quote=True)


def git_out(args, top):
    return subprocess.run(["git"] + args, cwd=top, capture_output=True, text=True).stdout.strip()


def records(doc, path):
    """[(check_id, pointer_prefix, node)] for the file and for list items, like verdict_records."""
    out = []
    if not isinstance(doc, dict):
        return out
    if isinstance(doc.get("status"), str) or isinstance(doc.get("metrics"), dict):
        out.append((path, "", doc))
    for lk in C.LIST_KEYS:
        items = doc.get(lk)
        if isinstance(items, list):
            for i, it in enumerate(items):
                if isinstance(it, dict) and (isinstance(it.get("status"), str) or isinstance(it.get("metrics"), dict)):
                    cid = it.get("id", it.get("name", i))
                    out.append(("%s#%s" % (path, cid), "/%s/%d" % (lk, i), it))
    return out


def collect(top, pattern):
    docs = C.json_files("worktree", top, pattern)
    checks, evidence_files = [], set()
    for path, doc in sorted(docs.items()):
        recs = records(doc, path)
        if not recs:
            continue
        evidence_files.add(path)
        ins = []
        for p, h in S.pairs(doc):
            rp = S.resolve(p, path, top)
            rel = os.path.relpath(rp, top) if rp else p
            ins.append((rel, "MISSING" if rp is None else ("OK" if C.sha256_file(rp) == h else "STALE")))
        pv = doc.get("provenance") if isinstance(doc.get("provenance"), dict) else {}
        last = git_out(["log", "-1", "--format=%h %cs %s", "--", path], top) or "untracked"
        dirty = bool(git_out(["status", "--porcelain", "--", path], top))
        for cid, pre, node in recs:
            ctx = {k: node.get(k, doc.get(k)) for k in ("scope", "assumptions", "limitations")}
            ctx_ptr = {k: (pre if k in node else "") + "/" + k for k in ctx}
            metrics = []
            for name, m in (node.get("metrics") or {}).items():
                if not isinstance(m, dict):
                    continue
                st = C.metric_status(m)
                mg = C.margin(m)
                tm = None
                if C._num(m.get("value")) and C._num(m.get("target")):
                    tm = (m["value"] - m["target"]) if m.get("sense", "min") != "max" else (m["target"] - m["value"])
                metrics.append({"name": name, "ptr": "%s/metrics/%s" % (pre, name), **{k: m.get(k) for k in
                                ("value", "limit", "target", "sense", "uncertainty")}, "status": st,
                                "margin": mg, "target_margin": tm})
            rec_st = node.get("status") if isinstance(node.get("status"), str) else None
            der = C.derived_status(node.get("metrics"))
            eff = rec_st or der or "UNKNOWN"
            if rec_st in C.RANK and der in C.RANK and C.RANK[der] < C.RANK[rec_st]:
                eff = der                       # show the worse of recorded and metric-derived
            flags = []
            if rec_st in C.RANK and der in C.RANK and C.RANK[rec_st] > C.RANK[der]:
                flags.append("INCONSISTENT")
            if not any(ctx.values()):
                flags.append("NO_CONTEXT")
            if metrics and any(not C._num(m["uncertainty"]) for m in metrics if m.get("limit") is not None):
                flags.append("NO_UNCERTAINTY")
            if not pv.get("command"):
                flags.append("NO_COMMAND")
            if any(s == "STALE" for _, s in ins):
                flags.append("STALE_INPUT")
            if any(s == "MISSING" for _, s in ins):
                flags.append("MISSING_INPUT")
            if not ins:
                flags.append("NO_INPUT_HASH")
            if not metrics:
                flags.append("NO_METRICS")
            q = QALIAS.get(str(node.get("question", doc.get("question", ""))).lower(), "unassigned")
            checks.append({"id": cid, "file": path, "pre": pre, "status": eff, "recorded": rec_st, "derived": der,
                           "layer": node.get("layer", doc.get("layer")), "question": q,
                           "part": node.get("part", doc.get("part")) or "(부품 미기재)",
                           "method": node.get("method", doc.get("method")), "metrics": metrics, "ctx": ctx,
                           "ctx_ptr": ctx_ptr, "criteria_source": doc.get("criteria_source"), "inputs": ins,
                           "command": pv.get("command"), "gen_commit": pv.get("commit"), "last": last,
                           "dirty": dirty, "sensitivity": node.get("sensitivity", doc.get("sensitivity")) or [],
                           "where": (node.get("data") or {}).get("where") if isinstance(node.get("data"), dict) else None,
                           "data_sql": (node.get("data") or {}).get("sql") if isinstance(node.get("data"), dict) else None,
                           "flags": flags, "doc": doc})
    return checks, evidence_files, docs


def sev(c):
    st = "INCONSISTENT" if "INCONSISTENT" in c["flags"] else c["status"]
    return SEVERITY.index(st) if st in SEVERITY else SEVERITY.index("UNKNOWN")


def worst(statuses):
    idx = [SEVERITY.index(s) if s in SEVERITY else SEVERITY.index("UNKNOWN") for s in statuses]
    return SEVERITY[min(idx)] if idx else None


def resolve_ref(top, ref):
    """'ev:file#/ptr' or 'ev:file#L4' -> (display value or None, note)."""
    r = ref[3:] if ref.startswith("ev:") else ref
    if "#" not in r:
        return None, "형식 오류"
    f, ptr = r.split("#", 1)
    full = os.path.join(top, f)
    if not os.path.isfile(full):
        return None, "파일 없음"
    try:
        if ptr.startswith("L"):
            lines = open(full, errors="replace").read().splitlines()
            n = int(ptr[1:])
            return (lines[n - 1].strip() if 1 <= n <= len(lines) else None), ("줄 없음" if not 1 <= n <= len(lines) else "")
        from hvp_cite import pointer
        v = pointer(json.load(open(full)), ptr)
        return (fmt(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)[:80]), ""
    except Exception as e:  # noqa: BLE001
        return None, "찾을 수 없음 (%s)" % type(e).__name__


def flatten_params(doc, pre=""):
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from flatten_params(v, pre + "/" + str(k).replace("~", "~0").replace("/", "~1"))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from flatten_params(v, "%s/%d" % (pre, i))
    else:
        yield pre, doc


def build(top, a):
    checks, evf, docs = collect(top, a.glob)
    checks.sort(key=lambda c: (sev(c), c["id"]))
    head = git_out(["rev-parse", "HEAD"], top)
    dirty = [l for l in git_out(["status", "--porcelain"], top).split("\n") if l.strip()]
    fp = hashlib.sha256("\n".join("%s %s" % (p, C.sha256_file(os.path.join(top, p))) for p in sorted(evf))
                        .encode()).hexdigest()
    decisions = []
    dpath = a.decisions
    if dpath and os.path.isfile(os.path.join(top, dpath)):
        try:
            decisions = json.load(open(os.path.join(top, dpath)))
            decisions = decisions.get("decisions", decisions) if isinstance(decisions, dict) else decisions
        except Exception:
            decisions = [{"id": "ERROR", "question": "decisions file is not valid JSON: %s" % dpath}]
    params = {}
    for c in checks:
        for p, s in c["inputs"]:
            if p in evf:
                continue
            params.setdefault(p, {"state": set(), "checks": []})
            params[p]["state"].add(s)
            params[p]["checks"].append(c)
    consensus = [(p, d) for p, d in sorted(docs.items()) if isinstance(d, dict) and isinstance(d.get("methods"), list)
                 and isinstance(d.get("pairs"), list)]
    meta = {"title": a.title or os.path.basename(top), "head": head, "dirty": dirty, "fingerprint": fp,
            "n_evidence": len(evf), "n_checks": len(checks),
            "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "command": "python3 <skill>/scripts/hvp_brief.py %s" % " ".join(
                ("'%s'" % x if " " in x else x) for x in sys.argv[1:])}
    return meta, checks, decisions, params, consensus


# ---------------------------------------------------------------- markdown (citable)

def cite(f, p):
    return "`ev:%s#%s`" % (f, p)


def md(meta, checks, decisions, params, consensus, top):
    L = []
    w = L.append
    w("# 검증·설계 판단 자료: %s" % meta["title"])
    w("")
    w("이 문서는 `hvp_brief.py`가 증거 파일에서 직접 만들었다. 숫자는 모두 증거를 가리키고, 에이전트가 쓴 숫자는 없다. "
      "판정은 증거 파일의 status와 지표가 정한다. 이 문서는 판단을 돕는 정리일 뿐이다.")
    w("")
    w("```")
    w("저장소 HEAD  %s%s" % (meta["head"][:12], "  (커밋 안 된 경로 %d개)" % len(meta["dirty"]) if meta["dirty"] else ""))
    w("증거 파일    %d개, 검사 %d개, 지문 %s" % (meta["n_evidence"], meta["n_checks"], meta["fingerprint"][:16]))
    w("생성        %s" % meta["generated"])
    w("재생성       %s" % meta["command"])
    w("이 문서 검사  python3 <skill>/scripts/hvp_cite.py check <이 파일> --rerun")
    w("```")
    w("")
    w("## 1. 먼저 볼 것")
    w("")
    att = [c for c in checks if c["status"] != "PASS" or c["flags"]]
    if not att:
        w("주의할 항목이 없다. 모든 검사가 PASS이고 맥락·불확도·생성 명령·입력 해시가 기록돼 있다.")
    for c in att:
        why = []
        if c["status"] != "PASS":
            why.append(c["status"])
        why += c["flags"]
        w("- `%s` — %s" % (c["id"], ", ".join(why)))
    w("")
    w("표시의 뜻: FAIL 하한 미달 · BLOCKED 판정 보류 · INCONSISTENT 기록된 status가 자기 지표보다 좋음 · "
      "INDETERMINATE 하한을 불확도보다 적게 넘음 · PASS_BELOW_TARGET 하한 이상 목표 미만 · "
      "STALE_INPUT 입력이 증거 작성 뒤 바뀜 · NO_CONTEXT 측정 범위·가정·한계 미기록 · "
      "NO_UNCERTAINTY 불확도 미기록 · NO_COMMAND 생성 명령 미기록 · NO_INPUT_HASH 어떤 설계의 값인지 확인 불가")
    w("")
    w("## 2. 결정 대기")
    w("")
    if not decisions:
        w("결정 파일이 없거나 비어 있다. 에이전트가 넘긴 결정 항목은 `verification/decisions.json`에 둔다(runbook 11절).")
    for d in decisions:
        w("### %s %s" % (d.get("id", ""), d.get("question", "")))
        w("")
        for o in d.get("options", []):
            w("- **%s**: %s | 비용 %s | 되돌리기 %s" % (o.get("name", ""), o.get("effect", ""), o.get("cost", "-"),
                                                    o.get("reversible", "-")))
            for e in o.get("evidence", []):
                v, note = resolve_ref(top, e)
                if v is None:
                    w("  - 근거(%s): %s" % (note, e.replace("ev:", "ev\u200b:")))
                elif e.split("#", 1)[1].startswith("L"):
                    w("  - 근거 원문: \"%s\" `%s`" % (v, e))
                else:
                    w("  - 근거 현재 값: %s `%s`" % (v, e))
        if d.get("recommendation"):
            w("- 에이전트 추천: %s (이유: %s)" % (d["recommendation"], d.get("reason", "-")))
        if d.get("blocks"):
            w("- 결정 전 멈춘 작업: %s" % ", ".join(d["blocks"]))
        w("- 상태: %s" % d.get("status", "open"))
        w("")
    w("## 3. 커버리지: 부품 × 질문")
    w("")
    parts = sorted({c["part"] for c in checks})
    w("| 부품 | " + " | ".join(lbl for _, lbl in QUESTIONS) + " | 질문 미지정 |")
    w("| --- |" + " --- |" * (len(QUESTIONS) + 1))
    for p in parts:
        row = []
        for q, _ in QUESTIONS + [("unassigned", "")]:
            cs = [c for c in checks if c["part"] == p and c["question"] == q]
            row.append("%s (%d)" % (worst([c["status"] for c in cs]), len(cs)) if cs else "검사 없음")
        w("| %s | %s |" % (p, " | ".join(row)))
    w("")
    w("\"검사 없음\" 칸은 그 질문에 대한 근거가 없다는 뜻이다. PASS가 아니다.")
    w("")
    w("## 4. 검사별")
    w("")
    for c in checks:
        f = c["file"]
        w("### %s" % c["id"])
        w("")
        if c["recorded"] and c["recorded"] != c["status"]:
            st = "%s (지표 기준; 기록된 status는 %s %s)" % (c["status"], c["recorded"], cite(f, c["pre"] + "/status"))
        elif c["recorded"]:
            st = "%s %s" % (c["status"], cite(f, c["pre"] + "/status"))
        else:
            st = "%s (지표에서 유도)" % c["status"]
        w("- 판정: %s | 층 %s | 질문 %s | 부품 %s | 방법 %s" % (st, c["layer"] or "-", c["question"], c["part"],
                                                     c["method"] or "-"))
        if c["flags"]:
            w("- 표시: %s" % ", ".join(c["flags"]))
        if c["metrics"]:
            w("")
            w("| 지표 | 값 | 하한 | 목표 | 불확도 | 방향 | 지표 판정 | 하한 여유(계산) |")
            w("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for m in c["metrics"]:
                cell = lambda k: ("%s %s" % (fmt(m[k]), cite(f, m["ptr"] + "/" + k))) if m.get(k) is not None else "-"  # noqa: E731
                mg = "-" if m["margin"] is None else fmt(m["margin"])
                w("| %s | %s | %s | %s | %s | %s | %s | %s |" % (m["name"], cell("value"), cell("limit"), cell("target"),
                                                               cell("uncertainty"), m.get("sense") or "min",
                                                               m["status"] or "-", mg))
            w("")
        for k, label in (("scope", "범위"), ("assumptions", "가정"), ("limitations", "한계")):
            v = c["ctx"][k]
            if v is None:
                w("- %s: 기록 없음" % label)
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    w("- %s: %s %s" % (label, x, cite(f, "%s/%d" % (c["ctx_ptr"][k], i))))
            else:
                w("- %s: %s %s" % (label, v, cite(f, c["ctx_ptr"][k])))
        if c["criteria_source"]:
            for k, v in sorted(c["criteria_source"].items()):
                w("- 기준 출처(%s): %s %s" % (k, v, cite(f, "/criteria_source/" + k.replace("~", "~0").replace("/", "~1"))))
        else:
            w("- 기준 출처: 기록 없음")
        for p, s in c["inputs"]:
            w("- 입력: `%s` %s" % (p, STATE_WORD.get(s, s)))
        if not c["inputs"]:
            w("- 입력: 해시 기록 없음 — 이 값이 어느 설계의 값인지 파일로 확인할 수 없다")
        for i, sv in enumerate(c["sensitivity"]):
            if isinstance(sv, dict):
                w("- 감도: %s → %s, 기울기 %s %s" % (sv.get("input"), sv.get("metric"), fmt(sv.get("slope")),
                                               cite(f, "%s/sensitivity/%d/slope" % (c["pre"], i))))
        if c["where"]:
            w("- 데이터 위치(값이 나온 곳): %s" % ", ".join("%s %s %s" % (k, fmt(v), cite(f, "%s/data/where/%s" % (c["pre"], k)))
                                                     for k, v in c["where"].items()))
        if c["data_sql"]:
            w("- 판정 SQL:")
            w("")
            w("  ```sql")
            for line in c["data_sql"].splitlines():
                w("  %s" % line)
            w("  ```")
            w("")
        if c["command"]:
            w("- 생성 명령:")
            w("")
            w("  ```")
            w("  %s" % c["command"])
            w("  ```")
            w("")
        else:
            w("- 생성 명령: 기록 없음")
        w("- 증거 파일: `%s`, 마지막 커밋 %s%s" % (f, c["last"], ", 커밋 안 된 변경 있음" if c["dirty"] else ""))
        w("- 직접 확인: `python3 <skill>/scripts/hvp_cite.py get %s %s --rerun`" % (
            f, (c["metrics"][0]["ptr"] + "/value") if c["metrics"] else (c["pre"] + "/status")))
        w("")
    w("## 5. 설계 입력과 영향받는 검사")
    w("")
    if not params:
        w("검사들이 해시로 기록한 설계 입력이 없다. 설계 값과 판정의 연결을 파일로 볼 수 없다.")
    for p, info in sorted(params.items()):
        w("### `%s` (%s)" % (p, ", ".join(STATE_WORD.get(x, x) for x in sorted(info["state"]))))
        w("")
        full = os.path.join(top, p)
        if p.endswith(".json") and os.path.isfile(full) and os.path.getsize(full) < 8192:
            try:
                for ptr, v in flatten_params(json.load(open(full))):
                    w("- %s = %s %s" % (ptr.lstrip("/"), fmt(v), cite(p, ptr)))
            except Exception:
                w("- (JSON을 읽지 못함)")
        else:
            w("- 내용은 파일에서 확인: `%s`" % p)
        for c in info["checks"]:
            ms = ", ".join("%s %s" % (m["name"], m["status"] or "-") for m in c["metrics"]) or c["status"]
            w("- 이 입력을 쓰는 검사: `%s` %s (%s)" % (c["id"], c["status"], ms))
        w("")
    w("## 6. 독립 방법의 합의")
    w("")
    if not consensus:
        w("합의 판정 증거(`hvp_consensus.py --out`)가 없다. 중요한 판정이 한 가지 방법에만 기대고 있을 수 있다.")
    for p, d in consensus:
        w("### `%s` — %s" % (p, d.get("status")))
        for i, m in enumerate(d["methods"]):
            w("- %s: %s %s ± %s %s" % (m.get("method"), fmt(m.get("value")), cite(p, "/methods/%d/value" % i),
                                       fmt(m.get("U")), cite(p, "/methods/%d/U" % i)))
        for i, pr in enumerate(d["pairs"]):
            w("- %s 대 %s: E_n %s %s" % (pr.get("a"), pr.get("b"), fmt(pr.get("En")), cite(p, "/pairs/%d/En" % i)))
        w("")
    w("## 7. 이 자료가 말하지 못하는 것")
    w("")
    w("- 증거 파일에 없는 검사, 저장소 밖(원격 GPU, 상용 GUI)에서만 있는 결과")
    w("- 해시로 기록되지 않은 의존(검사기 코드 안의 상수, 파일 이름만 읽는 입력)")
    w("- 범위·가정·한계 문구가 실제 검사기 동작과 맞는지(문구는 검사기가 쓴 것이고, 이 도구는 옮기기만 한다)")
    w("- 검사기 자체가 맞는지: known-answer 시험과 `--rerun` 재현으로 따로 확인한다")
    w("- 실물: 가상 검증 결과는 실측 보정 전에는 VERIFIED이고 VALIDATED가 아니다")
    w("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- html (to read)

CSS = """
:root{--bg:#ffffff;--fg:#1d1d1f;--mut:#5f6368;--line:#e3e3e8;--card:#f7f7f9;--fail:#c62828;--warn:#b26a00;
--ok:#2e7d32;--blk:#6a1b9a;--na:#8a8a8f;--bar:#e9e9ee;--val:#1d1d1f}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--mut:#a0a0a8;--line:#2c2c31;--card:#1c1c20;
--fail:#ef7070;--warn:#e0a640;--ok:#6cc070;--blk:#c28ae0;--na:#77777d;--bar:#2a2a30;--val:#ececf0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:36px 0 12px;padding-top:12px;border-top:1px solid var(--line)}h3{font-size:16px;margin:0 0 8px}
.mut{color:var(--mut)}code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;font-size:13px;margin:12px 0}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;color:#fff;margin:0 4px 4px 0}
.FAIL,.INCONSISTENT{background:var(--fail)}.BLOCKED{background:var(--blk)}.INDETERMINATE,.PASS_BELOW_TARGET,
.PASS_BY_MODEL_CHANGE{background:var(--warn)}.PASS{background:var(--ok)}.UNKNOWN,.NOT_RUN,.NONE{background:var(--na)}
.flag{display:inline-block;padding:0 6px;border:1px solid var(--line);border-radius:6px;font-size:12px;margin:0 4px 4px 0;color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}th,td{border-bottom:1px solid var(--line);
padding:5px 8px;text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600}
.scroll{overflow-x:auto}.bar{position:relative;height:22px;background:var(--bar);border-radius:4px;min-width:180px}
.mk{position:absolute;top:0;bottom:0;width:2px}.mk.l{background:var(--fail)}.mk.t{background:var(--ok)}
.unc{position:absolute;top:6px;height:10px;background:var(--mut);opacity:.35;border-radius:2px}
.dot{position:absolute;top:5px;width:12px;height:12px;margin-left:-6px;border-radius:50%;background:var(--val)}
.legend{font-size:12px;color:var(--mut)}ul{margin:4px 0 4px 18px;padding:0}li{margin:2px 0}
.cov td{text-align:center}.cov td:first-child{text-align:left}
h1,h3,p,li,code,.meta span{overflow-wrap:anywhere}.meta{grid-template-columns:max-content minmax(0,1fr)}
td code,th code{overflow-wrap:normal;white-space:nowrap}.scroll table{min-width:620px}
@media (max-width:600px){main{padding:16px}h1{font-size:20px}.meta{grid-template-columns:1fr}td,th{padding:4px 6px}}
"""


def pill(s):
    return '<span class="pill %s">%s</span>' % (esc(s if s in SEVERITY else "UNKNOWN"), esc(s))


def bar(m):
    vals = [m[k] for k in ("value", "limit", "target") if C._num(m.get(k))]
    if not C._num(m.get("value")) or len(vals) < 2:
        return ""
    u = abs(m["uncertainty"]) if C._num(m.get("uncertainty")) else 0
    lo, hi = min(vals + [m["value"] - u]), max(vals + [m["value"] + u])
    span = (hi - lo) or 1.0
    lo, hi = lo - 0.12 * span, hi + 0.12 * span
    pos = lambda x: 100.0 * (x - lo) / (hi - lo)  # noqa: E731
    out = ['<div class="bar" title="value %s, limit %s, target %s, U %s">' % (fmt(m["value"]), fmt(m.get("limit")),
                                                                               fmt(m.get("target")), fmt(m.get("uncertainty")))]
    if u:
        out.append('<div class="unc" style="left:%.2f%%;width:%.2f%%"></div>' % (pos(m["value"] - u), pos(m["value"] + u) - pos(m["value"] - u)))
    if C._num(m.get("limit")):
        out.append('<div class="mk l" style="left:%.2f%%"></div>' % pos(m["limit"]))
    if C._num(m.get("target")):
        out.append('<div class="mk t" style="left:%.2f%%"></div>' % pos(m["target"]))
    out.append('<div class="dot" style="left:%.2f%%"></div></div>' % pos(m["value"]))
    return "".join(out)


def page(meta, checks, decisions, params, consensus, top, md_name):
    H = []
    w = H.append
    w('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">')
    w("<title>판단 자료 — %s</title><style>%s</style></head><body><main>" % (esc(meta["title"]), CSS))
    w("<h1>검증·설계 판단 자료: %s</h1>" % esc(meta["title"]))
    w('<p class="mut">증거 파일에서 직접 만든 자료다. 에이전트가 쓴 숫자는 없다. 같은 내용에 숫자마다 출처를 붙인 판은 '
      '<code>%s</code>이고, <code>hvp_cite.py check %s --rerun</code>으로 검사할 수 있다.</p>' % (esc(md_name), esc(md_name)))
    w('<div class="meta"><span class="mut">HEAD</span><code>%s</code><span class="mut">커밋 안 된 경로</span><span>%d</span>'
      '<span class="mut">증거</span><span>파일 %d, 검사 %d, 지문 <code>%s</code></span><span class="mut">생성</span><span>%s</span>'
      '<span class="mut">재생성</span><code>%s</code></div>' % (esc(meta["head"][:12]), len(meta["dirty"]), meta["n_evidence"],
                                                              meta["n_checks"], esc(meta["fingerprint"][:16]),
                                                              esc(meta["generated"]), esc(meta["command"])))
    counts = {}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    w("<p>%s</p>" % " ".join("%s %d" % (pill(s), counts[s]) for s in SEVERITY if s in counts))

    w("<h2>1. 먼저 볼 것</h2>")
    att = [c for c in checks if c["status"] != "PASS" or c["flags"]]
    if not att:
        w("<p>주의할 항목이 없다.</p>")
    else:
        w('<div class="scroll"><table><tr><th>검사</th><th>판정</th><th>표시</th><th>가장 나쁜 지표</th></tr>')
        for c in att:
            wm = min(c["metrics"], key=lambda m: SEVERITY.index(m["status"]) if m["status"] in SEVERITY else 99) if c["metrics"] else None
            wtxt = "%s = %s (하한 %s, 목표 %s)" % (wm["name"], fmt(wm["value"]), fmt(wm.get("limit")), fmt(wm.get("target"))) if wm else "-"
            w('<tr><td><a href="#%s"><code>%s</code></a></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                esc(c["id"]), esc(c["id"]), pill(c["status"]), "".join('<span class="flag">%s</span>' % esc(f) for f in c["flags"]), esc(wtxt)))
        w("</table></div>")
    w('<p class="legend">INCONSISTENT 기록된 status가 자기 지표보다 좋음 · INDETERMINATE 하한을 불확도보다 적게 넘음 · STALE_INPUT 입력이 증거 뒤에 바뀜 · '
      'NO_CONTEXT 측정 범위·가정·한계 없음 · NO_UNCERTAINTY 불확도 없음 · NO_COMMAND 생성 명령 없음 · NO_INPUT_HASH 어느 설계의 값인지 확인 불가</p>')

    w("<h2>2. 결정 대기</h2>")
    if not decisions:
        w('<p class="mut">결정 파일이 없거나 비어 있다(<code>verification/decisions.json</code>).</p>')
    for d in decisions:
        w('<div class="card"><h3>%s %s</h3><div class="scroll"><table><tr><th>선택지</th><th>효과</th><th>비용</th><th>되돌리기</th><th>근거</th></tr>' % (
            esc(d.get("id", "")), esc(d.get("question", ""))))
        for o in d.get("options", []):
            w("<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                esc(o.get("name", "")), esc(o.get("effect", "")), esc(o.get("cost", "-")), esc(o.get("reversible", "-")),
                "<br>".join("<code>%s</code> → <b>%s</b>" % (esc(e), esc(resolve_ref(top, e)[0] or resolve_ref(top, e)[1]))
                            for e in o.get("evidence", []))))
        w("</table></div>")
        if d.get("recommendation"):
            w("<p>에이전트 추천: <b>%s</b> <span class=\"mut\">— %s</span></p>" % (esc(d["recommendation"]), esc(d.get("reason", ""))))
        if d.get("blocks"):
            w("<p class=\"mut\">결정 전 멈춘 작업: %s</p>" % esc(", ".join(d["blocks"])))
        w("<p class=\"mut\">상태: %s</p></div>" % esc(d.get("status", "open")))

    w("<h2>3. 커버리지: 부품 × 질문</h2>")
    parts = sorted({c["part"] for c in checks})
    w('<div class="scroll"><table class="cov"><tr><th>부품</th>%s<th>질문 미지정</th></tr>' % "".join("<th>%s</th>" % esc(l) for _, l in QUESTIONS))
    for p in parts:
        cells = []
        for q, _ in QUESTIONS + [("unassigned", "")]:
            cs = [c for c in checks if c["part"] == p and c["question"] == q]
            cells.append("<td>%s<span class=\"mut\">%d</span></td>" % (pill(worst([c["status"] for c in cs])), len(cs)) if cs
                         else '<td class="mut">검사 없음</td>')
        w("<tr><td>%s</td>%s</tr>" % (esc(p), "".join(cells)))
    w('</table></div><p class="legend">"검사 없음"은 근거가 없다는 뜻이다. PASS가 아니다. 숫자는 그 칸의 검사 수.</p>')

    w("<h2>4. 검사별</h2>")
    w('<p class="legend">막대: 점 = 값, 회색 = 불확도 범위, 빨강 = 하한, 초록 = 목표.</p>')
    for c in checks:
        w('<div class="card" id="%s"><h3><code>%s</code> %s</h3>' % (esc(c["id"]), esc(c["id"]), pill(c["status"])))
        w('<p class="mut">층 %s · 질문 %s · 부품 %s · 방법 %s%s</p>' % (esc(c["layer"] or "-"), esc(c["question"]), esc(c["part"]),
                                                                esc(c["method"] or "-"),
                                                                "" if c["recorded"] == c["status"] else
                                                                " · 지표 기준 판정 (기록된 status: %s)" % esc(c["recorded"]) if c["recorded"]
                                                                else " · status는 지표에서 유도"))
        if c["flags"]:
            w("<p>%s</p>" % "".join('<span class="flag">%s</span>' % esc(f) for f in c["flags"]))
        if c["metrics"]:
            w('<div class="scroll"><table><tr><th>지표</th><th>값</th><th>하한</th><th>목표</th><th>불확도</th><th>방향</th><th>판정</th><th>하한 여유</th><th style="width:30%">위치</th></tr>')
            for m in c["metrics"]:
                w("<tr><td><code>%s</code></td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    esc(m["name"]), esc(fmt(m["value"])), esc(fmt(m.get("limit"))), esc(fmt(m.get("target"))),
                    esc(fmt(m.get("uncertainty"))), esc(m.get("sense") or "min"), pill(m["status"] or "UNKNOWN"),
                    esc(fmt(m["margin"])), bar(m)))
            w("</table></div>")
        w("<ul>")
        for k, label in (("scope", "측정 범위"), ("assumptions", "가정"), ("limitations", "보지 않은 것")):
            v = c["ctx"][k]
            if v is None:
                w("<li><b>%s</b>: <span class=\"mut\">기록 없음</span></li>" % label)
            elif isinstance(v, list):
                w("<li><b>%s</b>: %s</li>" % (label, "; ".join(esc(x) for x in v)))
            else:
                w("<li><b>%s</b>: %s</li>" % (label, esc(v)))
        cs = c["criteria_source"]
        w("<li><b>기준 출처</b>: %s</li>" % ("; ".join("%s — %s" % (esc(k), esc(v)) for k, v in sorted(cs.items())) if cs
                                          else '<span class="mut">기록 없음</span>'))
        w("<li><b>입력</b>: %s</li>" % ("; ".join("<code>%s</code> %s" % (esc(p), esc(STATE_WORD.get(s, s))) for p, s in c["inputs"]) if c["inputs"]
                                      else '<span class="mut">해시 기록 없음: 어느 설계의 값인지 확인할 수 없다</span>'))
        for sv in c["sensitivity"]:
            if isinstance(sv, dict):
                w("<li><b>감도</b>: %s → %s, 기울기 %s</li>" % (esc(sv.get("input")), esc(sv.get("metric")), esc(fmt(sv.get("slope")))))
        if c["where"]:
            w("<li><b>데이터 위치</b>: %s <span class=\"mut\">(Foxglove 등으로 그 실행의 그 시각을 열어 볼 수 있다)</span></li>" % esc(
                ", ".join("%s = %s" % (k, fmt(v)) for k, v in c["where"].items())))
        if c["data_sql"]:
            w("<li><b>판정 SQL</b>: <code>%s</code></li>" % esc(" ".join(c["data_sql"].split())))
        w("<li><b>생성 명령</b>: %s</li>" % ("<code>%s</code>" % esc(c["command"]) if c["command"] else '<span class="mut">기록 없음</span>'))
        w("<li><b>증거</b>: <code>%s</code>, 마지막 커밋 %s%s</li>" % (esc(c["file"]), esc(c["last"]), " · 커밋 안 된 변경 있음" if c["dirty"] else ""))
        w("<li><b>직접 확인</b>: <code>python3 &lt;skill&gt;/scripts/hvp_cite.py get %s %s --rerun</code></li></ul></div>" % (
            esc(c["file"]), esc((c["metrics"][0]["ptr"] + "/value") if c["metrics"] else c["pre"] + "/status")))

    w("<h2>5. 설계 입력과 영향받는 검사</h2>")
    if not params:
        w('<p class="mut">검사들이 해시로 기록한 설계 입력이 없다.</p>')
    for p, info in sorted(params.items()):
        w('<div class="card"><h3><code>%s</code> <span class="flag">%s</span></h3>' % (esc(p), esc(", ".join(STATE_WORD.get(x, x) for x in sorted(info["state"])))))
        full = os.path.join(top, p)
        if p.endswith(".json") and os.path.isfile(full) and os.path.getsize(full) < 8192:
            try:
                rows = list(flatten_params(json.load(open(full))))
                w('<div class="scroll"><table><tr><th>항목</th><th>현재 값</th></tr>%s</table></div>' % "".join(
                    "<tr><td><code>%s</code></td><td><b>%s</b></td></tr>" % (esc(k.lstrip("/")), esc(fmt(v))) for k, v in rows))
            except Exception:
                w('<p class="mut">JSON을 읽지 못함</p>')
        w("<p>이 입력을 쓰는 검사: %s</p></div>" % " ".join('<a href="#%s"><code>%s</code></a> %s' % (
            esc(c["id"]), esc(c["id"]), pill(c["status"])) for c in info["checks"]))

    w("<h2>6. 독립 방법의 합의</h2>")
    if not consensus:
        w('<p class="mut">합의 판정 증거가 없다. 중요한 판정이 한 가지 방법에만 기대고 있을 수 있다.</p>')
    for p, d in consensus:
        w('<div class="card"><h3><code>%s</code> %s</h3><table><tr><th>방법</th><th>값</th><th>U</th></tr>%s</table>'
          '<table><tr><th>쌍</th><th>E_n</th><th></th></tr>%s</table></div>' % (
              esc(p), pill(d.get("status") or "UNKNOWN"),
              "".join("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (esc(m.get("method")), esc(fmt(m.get("value"))), esc(fmt(m.get("U"))))
                      for m in d["methods"]),
              "".join("<tr><td>%s 대 %s</td><td>%s</td><td>%s</td></tr>" % (esc(x.get("a")), esc(x.get("b")), esc(fmt(x.get("En"))),
                                                                            "합의" if x.get("agree") else "<b>불일치</b>") for x in d["pairs"])))
    w("<h2>7. 이 자료가 말하지 못하는 것</h2><ul>")
    for t in ("증거 파일에 없는 검사, 저장소 밖(원격 GPU, 상용 GUI)에서만 있는 결과",
              "해시로 기록되지 않은 의존(검사기 코드 안의 상수, 파일 이름만 읽는 입력)",
              "범위·가정·한계 문구가 실제 검사기 동작과 맞는지: 문구는 검사기가 쓴 것이고 이 도구는 옮기기만 한다",
              "검사기 자체가 맞는지: known-answer 시험과 --rerun 재현으로 따로 확인한다",
              "실물: 가상 검증 결과는 실측 보정 전에는 VERIFIED이고 VALIDATED가 아니다"):
        w("<li>%s</li>" % esc(t))
    w("</ul></main></body></html>")
    return "\n".join(H)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="docs/verification")
    ap.add_argument("--glob", default="")
    ap.add_argument("--decisions", default="verification/decisions.json")
    ap.add_argument("--title")
    a = ap.parse_args()
    top = C.toplevel()
    meta, checks, decisions, params, consensus = build(top, a)
    od = os.path.join(top, a.out_dir)
    os.makedirs(od, exist_ok=True)
    mdp, htp = os.path.join(od, "brief.md"), os.path.join(od, "brief.html")
    open(mdp, "w").write(md(meta, checks, decisions, params, consensus, top))
    open(htp, "w").write(page(meta, checks, decisions, params, consensus, top, os.path.relpath(mdp, top)))
    att = [c for c in checks if c["status"] != "PASS" or c["flags"]]
    print("BRIEF    %s" % os.path.relpath(htp, top))
    print("CITABLE  %s   (check: hvp_cite.py check %s --rerun)" % (os.path.relpath(mdp, top), os.path.relpath(mdp, top)))
    print("CHECKS   %d in %d evidence files; needing attention %d; open decisions %d" % (
        len(checks), meta["n_evidence"], len(att), sum(1 for d in decisions if d.get("status", "open") == "open")))
    for c in att[:15]:
        print("  %-24s %s" % (c["status"], c["id"] + ("  [" + ",".join(c["flags"]) + "]" if c["flags"] else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Known-answer self test for the hvp_* scripts.

  python3 selftest.py            run all cases in a throwaway git repository
  python3 selftest.py -v         show script output

Each case plants a defect with a known answer and checks that the script reports exactly
that. Run it after editing any script, and once on a new machine before trusting the tools.
Exit code 0 when every case passes.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
VERBOSE = "-v" in sys.argv
RESULTS = []


def run(args, cwd, env=None, check_rc=None):
    p = subprocess.run([PY, os.path.join(HERE, args[0])] + args[1:], cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    if VERBOSE:
        print("$", " ".join(args), "->", p.returncode)
        print(p.stdout.rstrip())
        if p.stderr.strip():
            print(p.stderr.rstrip())
    return p.returncode, p.stdout + p.stderr


def sh(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, shell=True, check=True, capture_output=True)


def case(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + detail[:400]))


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def new_repo(root, name):
    r = os.path.join(root, name)
    os.makedirs(os.path.join(r, "results"))
    sh("git init -q && git config user.email t@t && git config user.name t && git config commit.gpgsign false", r)
    open(os.path.join(r, "results/.keep"), "w").write("")
    sh("git add -A && git commit -qm init", r)
    return r


def metric(v, lim, tgt, sense="min"):
    return {"value": v, "limit": lim, "target": tgt, "sense": sense}


def fix_cases(root):
    # ------------------------------------------------ verdicts
    rv = new_repo(root, "repo-verdicts")
    W = lambda rel, obj: write_json(os.path.join(rv, rel), obj)  # noqa: E731
    W("results/clear.json", {"status": "FAIL", "layer": "L2", "metrics": {"min_clearance_mm": metric(-0.45, 0.2, 0.5)}})
    W("results/strength.json", {"status": "PASS", "layer": "L3", "metrics": {"sf": metric(1.8, 1.0, 1.5)}})
    W("results/insert.json", {"status": "PASS", "layer": "L3", "metrics": {"sf": metric(2.0, 1.0, 1.5)}})
    W("results/motion.json", {"status": "PASS", "layer": "L1"})
    W("results/crit.json", {"status": "PASS", "metrics": {"deflection_mm": metric(0.3, 0.5, 0.4, "max")}})
    W("results/multi.json", {"checks": [{"id": "mesh", "status": "PASS", "metrics": {"contact_ratio": metric(1.4, 1.2, 1.3)}},
                                        {"id": "backlash", "status": "FAIL", "metrics": {"b_mm": metric(0.3, 0.2, 0.1, "max")}}]})
    W("results/notes.json", {"comment": "no verdict here"})
    sh("git add -A && git commit -qm before", rv)
    base_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rv, capture_output=True, text=True).stdout.strip()
    W("results/clear.json", {"status": "PASS", "layer": "L2", "metrics": {"min_clearance_mm": metric(0.6, 0.2, 0.5)}})
    W("results/strength.json", {"status": "PASS_BELOW_TARGET", "layer": "L3", "metrics": {"sf": metric(1.2, 1.0, 1.5)}})
    W("results/insert.json", {"status": "PASS", "layer": "L3", "metrics": {"sf": metric(1.7, 1.0, 1.5)}})
    os.remove(os.path.join(rv, "results/motion.json"))
    W("results/crit.json", {"status": "PASS", "metrics": {"deflection_mm": metric(0.3, 0.8, 0.4, "max")}})
    W("results/multi.json", {"checks": [{"id": "mesh", "status": "PASS", "metrics": {"contact_ratio": metric(1.1, 1.2, 1.3)}},
                                        {"id": "backlash", "status": "FAIL", "metrics": {"b_mm": metric(0.3, 0.2, 0.1, "max")}}]})
    rc, out = run(["hvp_verdicts.py", "--json"], rv)
    o = json.loads(out[out.index("{"):]) if "{" in out else {"checks": {}}
    ck = o.get("checks", {})
    g = lambda cid: (ck.get(cid, {}).get("class"), ck.get(cid, {}).get("flags", []))  # noqa: E731
    case("verdicts: FAIL->PASS is FIXED, PASS->PASS_BELOW_TARGET is REGRESSED",
         g("results/clear.json")[0] == "FIXED" and g("results/strength.json")[0] == "REGRESSED", out)
    case("verdicts: deleted evidence is LOST", g("results/motion.json")[0] == "LOST", out)
    case("verdicts: same status, margin 1.0 -> 0.7 is MARGIN_DROP", "MARGIN_DROP" in g("results/insert.json")[1], out)
    case("verdicts: loosened limit is CRITERIA_CHANGED, not a pass", "CRITERIA_CHANGED" in g("results/crit.json")[1], out)
    case("verdicts: PASS recorded over a failing metric is INCONSISTENT",
         "INCONSISTENT" in g("results/multi.json#mesh")[1] and g("results/multi.json#backlash")[0] == "UNCHANGED", out)
    case("verdicts: files without a verdict ignored; NOT_ACCEPTABLE exit 1",
         "results/notes.json" not in ck and rc == 1 and o.get("verdict") == "NOT_ACCEPTABLE", out)
    sh("git checkout -q -- results", rv)
    W("results/clear.json", {"status": "PASS", "layer": "L2", "metrics": {"min_clearance_mm": metric(0.6, 0.2, 0.5)}})
    rc, out = run(["hvp_verdicts.py", "--expect-fixed", "results/clear.json"], rv)
    case("verdicts: clean fix with --expect-fixed is ACCEPTABLE", rc == 0 and "VERDICT ACCEPTABLE" in out, out)
    W("results/clear.json", {"status": "PASS_BELOW_TARGET", "layer": "L2", "metrics": {"min_clearance_mm": metric(0.3, 0.2, 0.5)}})
    rc, out = run(["hvp_verdicts.py", "--expect-fixed", "results/clear.json"], rv)
    rc2, out2 = run(["hvp_verdicts.py", "--expect-fixed", "results/clear.json", "--accept-below-target"], rv)
    case("verdicts: below-target fix needs --accept-below-target", rc == 1 and "expected fix not reached" in out
         and rc2 == 0, out + out2)
    snap = os.path.join(root, "before-snap")
    os.makedirs(snap)
    subprocess.run("git archive HEAD results | tar -x -C '%s'" % snap, shell=True, cwd=rv, check=True)
    rc3, out3 = run(["hvp_verdicts.py", "--before", "dir:" + snap, "--after", "dir:.", "--expect-fixed",
                     "results/clear.json", "--accept-below-target"], rv)
    case("verdicts: dir: snapshots give the same answer as git revisions", rc3 == 0 and "FIXED" in out3
         and "results/clear.json" in out3, out3)
    sh("git checkout -q -- results", rv)

    # ------------------------------------------------ impact
    ri = new_repo(root, "repo-impact")
    os.makedirs(os.path.join(ri, "src")); os.makedirs(os.path.join(ri, "ev"))
    for n in "abc":
        open(os.path.join(ri, "src/%s.py" % n), "w").write("# %s\n" % n)
    write_json(os.path.join(ri, "ev/e1.json"), {"layer": "L2", "inputs": {"src/a.py": sha(os.path.join(ri, "src/a.py"))}})
    write_json(os.path.join(ri, "ev/e2.json"), {"layer": "L1", "inputs": {"ev/e1.json": sha(os.path.join(ri, "ev/e1.json"))}})
    write_json(os.path.join(ri, "ev/e3.json"), {"layer": "L0", "inputs": {"src/b.py": sha(os.path.join(ri, "src/b.py"))}})
    write_json(os.path.join(ri, "ev/e4.json"), {"note": "reads src/a.py at import"})
    sh("git add -A && git commit -qm ev", ri)
    rc, out = run(["hvp_impact.py", "src/a.py"], ri)
    lines = [l for l in out.splitlines() if l.startswith("IMPACTED")]
    case("impact: direct and transitive dependents, not unrelated evidence",
         any("ev/e1.json" in l for l in lines) and any("ev/e2.json" in l for l in lines)
         and not any("ev/e3.json" in l for l in lines), out)
    case("impact: dependency before dependent even across layers (L2 e1 before L1 e2)",
         len(lines) == 2 and lines[0].split()[2] == "ev/e1.json" and lines[1].split()[2] == "ev/e2.json", out)
    case("impact: text-only mention reported as MENTIONED", "MENTIONED ev/e4.json -> src/a.py" in out, out)
    rc, out = run(["hvp_impact.py", "src/c.py"], ri)
    case("impact: path recorded by no evidence is UNTRACKED_DEPENDENCY", "UNTRACKED_DEPENDENCY src/c.py" in out
         and "impacted=0" in out, out)
    open(os.path.join(ri, "src/b.py"), "w").write("# b2\n")
    rc, out = run(["hvp_impact.py", "--diff"], ri)
    case("impact --diff: uncommitted edit found", "ev/e3.json" in out and "CHANGED src/b.py" in out, out)
    sh("git checkout -q -- src", ri)

    # ------------------------------------------------ sweep
    rs = new_repo(root, "repo-sweep")
    os.makedirs(os.path.join(rs, "tools"))
    open(os.path.join(rs, "tools/clear.py"), "w").write(
        "import json,sys\nOFF=0.3\ngap=float(sys.argv[1])\n"
        "json.dump({'metrics':{'c':{'value':round(gap-OFF,6),'limit':0.0,'target':0.2,'sense':'min'}}},"
        "open('results/s.json','w'))\n")
    open(os.path.join(rs, "tools/peak.py"), "w").write(
        "import json,sys\ngap=float(sys.argv[1])\n"
        "json.dump({'metrics':{'c':{'value':round(0.2-10*(gap-0.4)**2,6),'limit':0.0,'target':0.1}}},"
        "open('results/s.json','w'))\n")
    sh("git add -A && git commit -qm tools", rs)
    sweep = ["hvp_sweep.py", "--cmd", "python3 tools/clear.py {value}", "--out", "results/s.json", "--metric", "c",
             "--values", "0.2,0.4,0.6"]
    rc, out = run(sweep, rs)
    case("sweep: statuses, first limit/target and monotonicity",
         rc == 0 and "FIRST_LIMIT 0.4" in out and "FIRST_TARGET 0.6" in out and "MONOTONIC increasing" in out
         and "TRANSITION between 0.2 (FAIL) and 0.4 (PASS_BELOW_TARGET)" in out, out)
    case("sweep: working tree untouched", not os.path.exists(os.path.join(rs, "results/s.json")), out)
    src = open(os.path.join(rs, "tools/clear.py")).read()
    # candidate = tracked edit + a new untracked module it imports
    open(os.path.join(rs, "tools/clear.py"), "w").write(src.replace("OFF=0.3", "from off import OFF"))
    open(os.path.join(rs, "tools/off.py"), "w").write("OFF=0.5\n")
    rc, out = run(sweep, rs)
    rc2, out2 = run(sweep + ["--commit", "HEAD"], rs)
    case("sweep: uncommitted candidate (edit + new file) is what gets evaluated; --commit uses the commit only",
         rc == 1 and "FIRST_TARGET none" in out and rc2 == 0 and "FIRST_TARGET 0.6" in out2, out + out2)
    sh("git checkout -q -- tools && rm -f tools/off.py", rs)
    rc, out = run(["hvp_sweep.py", "--cmd", "python3 tools/peak.py {value}", "--out", "results/s.json", "--metric", "c",
                   "--values", "0.2,0.4,0.6"], rs)
    case("sweep: non-monotonic response flagged", "MONOTONIC no" in out and "not monotonic" in out, out)
    rc, out = run(["hvp_sweep.py", "--cmd", "exit 1", "--out", "results/s.json", "--metric", "c", "--values", "1,2"], rs)
    case("sweep: failed run -> exit 2", rc == 2 and "RUN_FAILED" in out, out)
    wts = subprocess.run(["git", "worktree", "list"], cwd=rs, capture_output=True, text=True).stdout.strip().splitlines()
    case("sweep: temporary worktrees removed", len(wts) == 1, "\n".join(wts))

    # ------------------------------------------------ fixloop
    def vfile(name, rows):
        pth = os.path.join(root, name)
        write_json(pth, {"verdict": "NOT_ACCEPTABLE", "checks": {c: {"before": b, "after": a_, "class": k}
                                                                for c, (b, a_, k) in rows.items()}})
        return pth
    v1 = vfile("v1.json", {"A": ("FAIL", "PASS", "FIXED"), "B": ("PASS", "FAIL", "REGRESSED")})
    v1w = os.path.join(root, "v1w.json")
    write_json(v1w, {"verdict": "ACCEPTABLE", "checks": {
        "A": {"before": "FAIL", "after": "PASS", "class": "FIXED", "flags": []},
        "results/wall.json": {"before": "PASS", "after": "PASS", "class": "UNCHANGED", "flags": ["MARGIN_DROP"]}}})
    v2 = vfile("v2.json", {"A": ("FAIL", "FAIL", "UNCHANGED"), "B": ("PASS", "PASS", "UNCHANGED")})
    FL = lambda *x: run(["hvp_fixloop.py"] + list(x) + ["--sid", "F"], rv)  # noqa: E731
    FL("open", "--issue", "I1", "--checks", "A", "--cause", "A interferes")
    rc, out = FL("attempt", "--issue", "I1", "--change", "move A", "--params", "gap=0.4", "--verdicts", v1)
    case("fixloop: first attempt -> CONTINUE", rc == 0 and "CONTINUE 1/3" in out, out)
    rc, out = FL("attempt", "--issue", "I1", "--change", "move B back", "--params", "gap=0.5", "--verdicts", v2)
    case("fixloop: fix A breaks B, fix B breaks A -> STOP COUPLED", rc == 3 and "COUPLED A <-> B" in out
         and "OSCILLATING A" in out, out)
    FL("open", "--issue", "I2", "--checks", "A", "--cause", "x")
    FL("attempt", "--issue", "I2", "--change", "c1", "--params", "gap=0.4", "--verdicts", v1)
    FL("attempt", "--issue", "I2", "--change", "c2", "--params", "gap=0.7", "--verdicts", v1)
    rc, out = FL("attempt", "--issue", "I2", "--change", "c3", "--params", "gap=0.4", "--verdicts", v1)
    case("fixloop: same parameters tried again -> REPEAT", rc == 3 and "REPEAT attempt 3" in out, out)
    FL("open", "--issue", "I3", "--checks", "A", "--cause", "x", "--budget", "2")
    FL("attempt", "--issue", "I3", "--change", "c1", "--params", "gap=0.4", "--verdicts", v1)
    rc, out = FL("attempt", "--issue", "I3", "--change", "c2", "--params", "gap=0.6", "--verdicts", v1)
    case("fixloop: budget exhausted -> STOP BUDGET only", rc == 3 and "BUDGET 2/2" in out and "REPEAT" not in out
         and "COUPLED" not in out, out)
    FL("open", "--issue", "I4", "--checks", "A", "--cause", "x")
    FL("attempt", "--issue", "I4", "--change", "w", "--params", "gap=0.6", "--verdicts", v1w)
    rc, out4 = FL("show", "--issue", "I4", "--md")
    case("fixloop: flags such as MARGIN_DROP shown as other changes", "wall.json MARGIN_DROP" in out4, out4)
    rc, out = FL("show", "--md")
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=rv,
                            capture_output=True, text=True).stdout.strip()
    case("fixloop: ledger kept beside the session lock, markdown report",
         os.path.isfile(os.path.join(common, "hvp-sessions", "F.fixlog.json")) and "| 1 | move A | gap=0.4 |" in out, out)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=rv, capture_output=True, text=True).stdout.strip()
    case("fix tools leave the target repository unchanged", st == "", st)

    # ------------------------------------------------ red-green
    rg = new_repo(root, "repo-redgreen")
    os.makedirs(os.path.join(rg, "src"))
    open(os.path.join(rg, "src/cam.py"), "w").write("GAP = 0.30\n")
    sh("git add -A && git commit -qm base", rg)
    open(os.path.join(rg, "src/cam.py"), "w").write("GAP = 0.65\n")            # the fix (uncommitted)
    open(os.path.join(rg, "t_good.py"), "w").write(
        "import sys; sys.path.insert(0,'src'); import cam\n"
        "c = cam.GAP - 0.45\nassert c >= 0.15, 'min_clearance %.2f below target' % c\n")
    open(os.path.join(rg, "t_weak.py"), "w").write(
        "import sys; sys.path.insert(0,'src'); import cam\nassert cam.GAP > 0\n")
    open(os.path.join(rg, "t_broken.py"), "w").write("import nonexistent_module\n")
    rc, out = run(["hvp_redgreen.py", "--test", "python3 t_good.py", "--fix", "src/cam.py",
                   "--expect", "min_clearance"], rg)
    case("redgreen: a test that detects the defect -> RED_GREEN_OK", rc == 0 and "RESULT RED_GREEN_OK" in out, out)
    rc, out = run(["hvp_redgreen.py", "--test", "python3 t_weak.py", "--fix", "src/cam.py"], rg)
    case("redgreen: a test that passes without the fix -> TEST_DOES_NOT_DETECT",
         rc == 1 and "TEST_DOES_NOT_DETECT" in out, out)
    rc, out = run(["hvp_redgreen.py", "--test", "python3 t_good.py && python3 t_broken.py", "--fix", "src/cam.py"], rg)
    case("redgreen: failing even with the fix -> GREEN_FAILS", rc == 1 and "GREEN_FAILS" in out, out)
    open(os.path.join(rg, "t_other.py"), "w").write(
        "import sys; sys.path.insert(0,'src'); import cam\n"
        "if cam.GAP < 0.5: raise SystemExit('cannot open data/fixture.step')\n")
    rc, out = run(["hvp_redgreen.py", "--test", "python3 t_other.py", "--fix", "src/cam.py",
                   "--expect", "min_clearance"], rg)
    case("redgreen: red for an unrelated reason -> RED_FOR_OTHER_REASON", rc == 1 and "RED_FOR_OTHER_REASON" in out, out)
    sh("git add src/cam.py && git commit -qm fix", rg)
    base = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=rg, capture_output=True, text=True).stdout.strip()
    rc, out = run(["hvp_redgreen.py", "--test", "python3 t_good.py", "--fix", "src/cam.py", "--base", base], rg)
    case("redgreen: committed fix checked against --base", rc == 0 and "RED_GREEN_OK" in out, out)
    gl = subprocess.run(["git", "status", "--porcelain"], cwd=rg, capture_output=True, text=True).stdout
    wts = subprocess.run(["git", "worktree", "list"], cwd=rg, capture_output=True, text=True).stdout.strip().splitlines()
    case("redgreen: working tree and worktrees untouched", len(wts) == 1 and "src/cam.py" not in gl, gl + "\n".join(wts))

    # ------------------------------------------------ cite
    rc_ = new_repo(root, "repo-cite")
    os.makedirs(os.path.join(rc_, "params")); os.makedirs(os.path.join(rc_, "tools"))
    open(os.path.join(rc_, "params/cam.json"), "w").write('{"gap_mm": 0.6, "envelope_mm": 4.0}\n')
    open(os.path.join(rc_, "tools/check_wall.py"), "w").write(
        "import json,hashlib\nh=lambda p: hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
        "p=json.load(open('params/cam.json'))\nsf=round((p['envelope_mm']-2*p['gap_mm'])/2.1,4)\n"
        "json.dump({'status':'PASS','inputs':{'params/cam.json':h('params/cam.json')},"
        "'scope':'housing wall, static closing load 40 N','assumptions':['linear elastic','PETG E=2.0 GPa'],"
        "'limitations':['no fatigue','no creep'],'criteria_source':{'safety_factor':'customer spec'},"
        "'provenance':{'command':'python3 tools/check_wall.py'},"
        "'metrics':{'safety_factor':{'value':sf,'limit':1.0,'target':1.5,'sense':'min'}}},"
        "open('results/wall.json','w'))\n")
    sh("python3 tools/check_wall.py", rc_)
    write_json(os.path.join(rc_, "results/bare.json"), {"metrics": {"x": {"value": 3.0, "limit": 1.0}}})
    open(os.path.join(rc_, "bom.csv"), "w").write("id,part,qty\nP1,cam,1\nP2,M3x10 bolt,4\n")
    sh("git add -A && git commit -qm ev", rc_)
    WP = "/metrics/safety_factor/value"   # value 1.3333
    rc, out = run(["hvp_cite.py", "get", "results/wall.json", WP], rc_)
    case("cite get: context first, criteria, command, input state",
         rc == 0 and "VALUE    1.3333" in out and "SCOPE    \"housing wall" in out and "LIMITATI" in out
         and "CRITERIA limit 1.0, target 1.5" in out and "COMMAND  python3 tools/check_wall.py" in out
         and "INPUT    OK      params/cam.json" in out, out)
    rc, out = run(["hvp_cite.py", "get", "results/wall.json", WP, "--rerun"], rc_)
    case("cite get --rerun: recorded command reproduces the value", rc == 0 and "RERUN    REPRODUCED" in out, out)
    d = json.load(open(os.path.join(rc_, "results/wall.json"))); d["metrics"]["safety_factor"]["value"] = 9.9
    write_json(os.path.join(rc_, "results/wall.json"), d)
    rc, out = run(["hvp_cite.py", "get", "results/wall.json", WP, "--rerun"], rc_)
    open(os.path.join(rc_, "fake.md"), "w").write("SF 9.9 `ev:results/wall.json#%s`\n" % WP)
    rc2, out2 = run(["hvp_cite.py", "check", "fake.md", "--rerun"], rc_)
    case("cite --rerun: hand-edited evidence is NOT_REPRODUCED (get and check)",
         rc == 1 and "NOT_REPRODUCED" in out and rc2 == 1 and "NOT_REPRODUCED results/wall.json" in out2, out + out2)
    sh("git checkout -q -- results && rm -f fake.md", rc_)
    open(os.path.join(rc_, "clean.md"), "w").write(
        "# report\n"
        "wall SF 1.33 (target 1.5) `ev:results/wall.json#%s`\n"
        "SF 1.3333 PASS `ev:results/wall.json#%s` `ev:results/wall.json#/status`\n"
        "wall safety factor 1.333[^sf] per customer spec\n"
        "bolts M3x10, 4 pcs `ev:bom.csv#L3`\n"
        "```\nraw 3.5 mm inside code\n```\n"
        "run took 4 min on v5\n\n"
        "[^sf]: ev:results/wall.json#%s\n" % (WP, WP, WP))
    rc, out = run(["hvp_cite.py", "check", "clean.md", "--rerun"], rc_)
    case("cite check: value with target, adjacent cites, footnote, BOM line ref all accepted; rerun reproduces",
         rc == 0 and "citations_ok=5 problems=0 uncited=0 no_context=0 reproduced=1/1" in out, out)
    open(os.path.join(rc_, "dirty.md"), "w").write(
        "wall SF 1.45 `ev:results/wall.json#%s`\n"            # 1 wrong number
        "bolts 6 pcs `ev:bom.csv#L3`\n"                           # 2 number not in that line
        "margin ratio 0.33, enough\n"                             # 3 unitless ratio, uncited
        "the housing is sufficiently safe\n"                      # 4 judgment, uncited
        "x 3.0 `ev:results/bare.json#/metrics/x/value`\n"         # 5 no context
        "clearance 0.21 mm\n"                                     # 6 unit number, uncited
        "see 1.0[^nope]\n" % WP)                                  # 7 undefined footnote
    rc, out = run(["hvp_cite.py", "check", "dirty.md"], rc_)
    case("cite check: wrong number, wrong line ref, uncited ratio/judgment/unit, no context, bad footnote",
         rc == 1 and "MISMATCH line 1" in out and "MISMATCH line 2" in out and "UNCITED  line 3" in out
         and "UNCITED_JUDGMENT line 4" in out and "NO_CONTEXT line 5" in out and "UNCITED  line 6" in out
         and "MISSING  line 7" in out, out)
    return base_rev


def main():
    root = tempfile.mkdtemp(prefix="hvp-selftest-")
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    sh("git init -q && git config user.email t@t && git config user.name t && git config commit.gpgsign false", repo)
    os.makedirs(os.path.join(repo, "src"))
    os.makedirs(os.path.join(repo, "results"))
    open(os.path.join(repo, "src/gen.py"), "w").write("print(1)\n")
    open(os.path.join(repo, "results/.keep"), "w").write("")
    open(os.path.join(repo, ".gitignore"), "w").write("data/\n")
    sh("git add -A && git commit -qm init", repo)

    # ------------------------------------------------ compare
    a = {"status": "PASS", "bolt_head_diameter_mm": 7.0, "fault_duration_s": 0.1, "overhead_mm": 3.0,
         "elapsed_s": 1.2, "design_commit": "a" * 40, "volatile": {"tmp": "/tmp/x"},
         "inputs": {"src/gen.py": "b" * 64}, "residual": 1e-13, "parts": [{"head": "hex", "mass_kg": 1.0}]}
    b = json.loads(json.dumps(a))
    b.update(bolt_head_diameter_mm=9.0, fault_duration_s=5.0, overhead_mm=99, elapsed_s=7,
             design_commit="c" * 40, residual=3e-13)
    b["volatile"]["tmp"] = "/tmp/y"; b["inputs"]["src/gen.py"] = "d" * 64; b["parts"][0]["head"] = "socket"
    c = json.loads(json.dumps(a)); c.update(elapsed_s=9, design_commit="e" * 40, residual=2e-13)
    write_json(os.path.join(root, "A.json"), a); write_json(os.path.join(root, "B.json"), b)
    write_json(os.path.join(root, "C.json"), c)
    rc, out = run(["hvp_compare.py", os.path.join(root, "A.json"), os.path.join(root, "B.json")], repo)
    case("compare: physical keys containing head/duration are compared",
         rc == 1 and "DIFFERENT 4" in out and "bolt_head_diameter_mm" in out and "fault_duration_s" in out
         and "/parts[0]/head" in out and "overhead_mm" in out, out)
    case("compare: ignored keys are listed, hash-only change flagged",
         "design_commit" in out and "elapsed_s" in out and "/inputs/src/gen.py" in out, out)
    rc, out = run(["hvp_compare.py", os.path.join(root, "A.json"), os.path.join(root, "C.json")], repo)
    case("compare: volatile-only change and near-zero residual are EQUIVALENT", rc == 0 and "EQUIVALENT" in out, out)
    write_json(os.path.join(repo, "results/x.json"), a); sh("git add results/x.json && git commit -qm x", repo)
    write_json(os.path.join(repo, "results/x.json"), b)
    rc, out = run(["hvp_compare.py", "git:HEAD:results/x.json", "results/x.json"], repo)
    case("compare: git:<rev>:<path> spec", rc == 1 and "DIFFERENT 4" in out, out)
    sh("git rm -qf results/x.json && git commit -qm rm-x", repo)   # its fake input hash would read as STALE below

    # ------------------------------------------------ stale
    gen = os.path.join(repo, "src/gen.py")
    h = sha(gen)
    write_json(os.path.join(repo, "results/a.json"), {"inputs": {"src/gen.py": h}, "status": "PASS"})
    write_json(os.path.join(repo, "results/b.json"), {"input": {"path": "src/gen.py", "sha256": h}})
    hb = sha(os.path.join(repo, "results/b.json"))
    write_json(os.path.join(repo, "results/c.json"), {"inputs": {"results/b.json": hb}})
    sh("git add results && git commit -qm ev", repo)
    rc, out = run(["hvp_stale.py"], repo)
    case("stale: clean tree reports nothing", rc == 0 and "stale=0" in out, out)
    open(gen, "w").write("print(2)\n")
    rc, out = run(["hvp_stale.py"], repo)
    case("stale: both record shapes caught after a source edit",
         rc == 1 and "STALE   results/a.json -> src/gen.py" in out and "STALE   results/b.json -> src/gen.py" in out, out)
    rc, out = run(["hvp_stale.py", "--file", "src/gen.py"], repo)
    case("stale --file: lists evidence recording the source",
         "RECORDED_BY results/a.json" in out and "RECORDED_BY results/b.json" in out and "changed=True" in out, out)
    write_json(os.path.join(repo, "results/b.json"), {"input": {"path": "src/gen.py", "sha256": sha(gen)}})
    rc, out = run(["hvp_stale.py"], repo)
    case("stale: regenerating b exposes c, which records b", rc == 1 and "STALE   results/c.json -> results/b.json" in out, out)
    write_json(os.path.join(repo, "results/c.json"), {"inputs": {"results/b.json": "0" * 64}})
    write_json(os.path.join(repo, "results/b.json"), {"input": {"path": "src/gen.py", "sha256": h}})
    rc, out = run(["hvp_stale.py", "--json"], repo)
    o = json.loads(out[out.index("{"):]) if "{" in out else {}
    case("stale: ORDER puts b before c", o.get("order", []).index("results/b.json") < o.get("order", []).index("results/c.json")
         if {"results/b.json", "results/c.json"} <= set(o.get("order", [])) else False, out)
    sh("git checkout -q -- src results", repo)

    # ------------------------------------------------ session
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo,
                            capture_output=True, text=True).stdout.strip()
    L = os.path.join(common, "hvp-sessions"); os.makedirs(L, exist_ok=True)
    now = int(time.time())
    write_json(os.path.join(L, "other-active.json"), {"session": "other-active", "heartbeat": now - 60, "worktree": "/x"})
    write_json(os.path.join(L, "other-stale.json"), {"session": "other-stale", "heartbeat": now - 99999})
    rc, out = run(["hvp_session.py", "start", "--sid", "T"], repo)
    case("session start: ACTIVE lock -> exit 2, STALE listed", rc == 2 and "ACTIVE  other-active" in out and "STALE   other-stale" in out, out)
    os.remove(os.path.join(L, "other-active.json")); os.remove(os.path.join(L, "other-stale.json"))
    rc, out = run(["hvp_session.py", "isolate", "--sid", "T", "--commit", "HEAD"], repo)
    wt = out.strip().splitlines()[-1] if rc == 0 else ""
    case("session isolate: worktree created and recorded", rc == 0 and os.path.isdir(wt), out)
    rc, out = run(["hvp_session.py", "get", "--sid", "T", "worktree"], wt or repo)
    case("session get from inside the worktree", out.strip() == wt, out)
    rc, out = run(["hvp_session.py", "signals", "--sid", "T", "--minutes", "1"], wt or repo)
    case("session signals: own command and pipeline not reported", rc == 0 and "VERDICT: NONE" in out, out)
    sp = subprocess.Popen("cd %s && (sleep 30 &) " % wt, shell=True); sp.wait(); time.sleep(0.5)
    rc, out = run(["hvp_session.py", "signals", "--sid", "T", "--minutes", "1"], wt or repo)
    case("session signals: detached foreign process -> CONCURRENT", rc == 2 and "sleep 30" in out, out)
    subprocess.run("pkill -f 'sleep 30'", shell=True)

    # ------------------------------------------------ stagecheck (inside worktree)
    open(os.path.join(wt, "mine.txt"), "w").write("m")
    open(os.path.join(wt, "other.txt"), "w").write("o")
    with open(os.path.join(wt, "big.npz"), "wb") as f:
        f.write(b"\0" * (26 * 1024 * 1024))
    run(["hvp_session.py", "touch", "--sid", "T", "mine.txt"], wt)
    sh("git add mine.txt other.txt big.npz", wt)
    rc, out = run(["hvp_stagecheck.py", "--sid", "T"], wt)
    case("stagecheck: foreign file, >25 MB and raw output flagged",
         rc == 1 and "other.txt" in out and "big.npz" in out and "[2] staged larger than 25 MB: 1" in out
         and "[3] raw analysis output staged: 1" in out, out)
    sh("git restore --staged other.txt big.npz", wt)
    rc, out = run(["hvp_stagecheck.py", "--sid", "T"], wt)
    case("stagecheck: clean after unstaging", rc == 0 and "VERDICT: CLEAN" in out, out)

    # ------------------------------------------------ snapshot (inside linked worktree)
    open(os.path.join(wt, "check.py"), "w").write("import os,sys\nsys.exit(0 if os.path.exists('data/in.json') else 1)\n")
    os.makedirs(os.path.join(wt, "data")); open(os.path.join(wt, "data/in.json"), "w").write("{}")
    sh("git add check.py", wt)
    hooks = os.path.join(common, "hooks"); os.makedirs(hooks, exist_ok=True)
    open(os.path.join(hooks, "pre-push"), "w").write("#!/bin/sh\npython3 check.py\n")
    rc_wt = subprocess.run([PY, "check.py"], cwd=wt).returncode
    rc, out = run(["hvp_snapshot.py", "--show-hook", "--cmd", "python3 check.py"], wt)
    case("snapshot: hook found from a linked worktree", "python3 check.py" in out and "hooks dir:" in out, out)
    case("snapshot: passes in working tree, fails in snapshot (untracked input)", rc_wt == 0 and rc == 1, out)
    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True).stdout
    case("snapshot: refs untouched", head_before == subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt,
                                                                    capture_output=True, text=True).stdout)

    # ------------------------------------------------ session end
    rc, out = run(["hvp_session.py", "end", "--sid", "T"], wt)
    case("session end: dirty worktree refused, lock kept", rc == 1 and os.path.exists(os.path.join(L, "T.json")), out)
    sh("git reset -q && rm -rf mine.txt other.txt big.npz check.py data", wt)
    rc, out = run(["hvp_session.py", "end", "--sid", "T"], wt)
    case("session end: clean worktree removed from inside it, lock removed",
         rc == 0 and not os.path.isdir(wt) and not os.path.exists(os.path.join(L, "T.json")), out)

    # ------------------------------------------------ determinism
    probe = "s={'alpha','beta','gamma','delta','eps'};print(next(iter(s)))"
    first = {}
    for s in range(1, 200):
        first[s] = subprocess.run([PY, "-c", probe], env=dict(os.environ, PYTHONHASHSEED=str(s)),
                                  capture_output=True, text=True).stdout.strip()
    same_pair = next(((i, j) for i in first for j in first if i < j and first[i] == first[j]), None)
    diff_s = next((k for k in first if same_pair and first[k] != first[same_pair[0]]), None)
    tool = os.path.join(repo, "tools.py")
    open(tool, "w").write(
        "import json,os\n"
        "s={'alpha','beta','gamma','delta','eps'}\n"
        "need=os.environ.get('NEED_INPUT')\n"
        "if need and not os.path.exists('data/in.json'): raise SystemExit('missing data/in.json')\n"
        "json.dump({'pick':next(iter(s)),'elapsed_s':1.0},open('results/o.json','w'))\n")
    sh("git add tools.py && git commit -qm tool", repo)
    os.makedirs(os.path.join(repo, "data"), exist_ok=True); open(os.path.join(repo, "data/in.json"), "w").write("{}")
    if same_pair and diff_s:
        rc, out = run(["hvp_determinism.py", "--cmd", "python3 tools.py", "--out", "results/o.json",
                       "--seeds", "%d,%d" % same_pair], repo)
        case("determinism: two agreeing seeds report agreement, not proof", rc == 0 and "2 values agree" in out
             and "not a proof" in out, out)
        rc, out = run(["hvp_determinism.py", "--cmd", "python3 tools.py", "--out", "results/o.json",
                       "--seeds", "%d,%d,%d" % (same_pair[0], same_pair[1], diff_s), "--grep", "."], repo)
        case("determinism: a third seed exposes the order dependence", rc == 1 and "RESULT DIFFERENT" in out
             and "/pick" in out and "tools.py" in out, out)
    else:
        case("determinism: found seed pair for the probe", False, str(first)[:300])
    rc, out = run(["hvp_determinism.py", "--cmd", "NEED_INPUT=1 python3 tools.py", "--out", "results/o.json",
                   "--seeds", "1,2"], repo)
    case("determinism: missing untracked input -> RUN_FAILED", rc == 1 and "RUN_FAILED" in out, out)
    rc, out = run(["hvp_determinism.py", "--cmd", "NEED_INPUT=1 python3 tools.py", "--out", "results/o.json",
                   "--seeds", "1,2", "--inputs", "data/in.json"], repo)
    case("determinism: --inputs copies the untracked input", rc in (0, 1) and "RUN_FAILED" not in out
         and "inputs copied: data/in.json" in out, out)
    wts = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True).stdout.strip().splitlines()
    case("determinism: temporary worktrees removed", len(wts) == 1, "\n".join(wts))

    fix_cases(root)

    n_ok = sum(ok for _, ok in RESULTS)
    print("\nSELFTEST %d/%d passed (%s)" % (n_ok, len(RESULTS), root))
    if n_ok == len(RESULTS):
        subprocess.run(["rm", "-rf", root])
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

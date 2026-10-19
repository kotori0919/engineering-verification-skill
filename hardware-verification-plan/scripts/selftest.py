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

    n_ok = sum(ok for _, ok in RESULTS)
    print("\nSELFTEST %d/%d passed (%s)" % (n_ok, len(RESULTS), root))
    if n_ok == len(RESULTS):
        subprocess.run(["rm", "-rf", root])
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Environment for the data-layer tools (MCAP, Parquet, DuckDB, MCP), built on what the
project already uses (Nix, venv, conda) and pinned by one lock file.

  hvp_env.py detect               environment markers in the project and what the tools will use
  hvp_env.py check [--strict]     is a matching data environment available? (top-level pins;
                                  --strict: every package in the lock)
  hvp_env.py setup [--venv DIR]   create a venv from env/requirements-data.lock
                                  (default ~/.cache/hvp/venv-py<major><minor>-<lock hash>, outside the repo)
  hvp_env.py which                print the python that the data tools will run with
  hvp_env.py run <tool.py> [args] run a data tool (name relative to scripts/) with that python

Which python: the first of $HVP_PY, the current interpreter, the cache venv for this lock,
whose installed versions match the lock's top-level pins. Inside a project Nix shell built
from env/flake.nix, HVP_PY is exported by the shellHook.
The standard-library tools (everything else in scripts/) need none of this.
Exit codes: 0 ok, 2 no matching environment (the message says what to run), 3 error.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
LOCK = os.path.join(SKILL, "env", "requirements-data.lock")
TOP = os.path.join(SKILL, "env", "requirements-data.in")
MARKERS = [("flake.nix", "Nix flake"), ("flake.lock", "Nix flake lock"), ("shell.nix", "nix-shell"),
           ("default.nix", "Nix expression"), (".envrc", "direnv"), ("pyproject.toml", "Python project"),
           ("uv.lock", "uv lock"), ("poetry.lock", "Poetry lock"), ("requirements.txt", "pip requirements"),
           ("environment.yml", "conda environment"), (".python-version", "pyenv version"), (".venv", "venv")]


def pins(path):
    out = {}
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            k, v = line.split("==", 1)
            out[k.strip().lower().replace("_", "-")] = v.strip()
    return out


def lock_hash():
    return hashlib.sha256(open(LOCK, "rb").read()).hexdigest()[:12]


def cache_venv():
    base = os.environ.get("HVP_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "hvp"))
    return os.path.join(base, "venv-py%d%d-%s" % (sys.version_info[0], sys.version_info[1], lock_hash()))


def venv_python(d):
    for p in (os.path.join(d, "bin", "python"), os.path.join(d, "Scripts", "python.exe")):
        if os.path.exists(p):
            return p
    return None


PROBE = ("import importlib.metadata as m, json, sys\n"
         "out = {}\n"
         "for n in sys.argv[1:]:\n"
         "    try: out[n] = m.version(n)\n"
         "    except Exception: out[n] = None\n"
         "print(json.dumps(out))\n")


def installed(py, names):
    try:
        r = subprocess.run([py, "-c", PROBE] + list(names), capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout.strip() or "{}")
    except Exception:
        return {}


def compare(py, strict=False):
    want = pins(LOCK) if strict else pins(TOP)
    have = installed(py, want)
    bad = {k: (v, have.get(k)) for k, v in want.items() if have.get(k) != v}
    return bad


def candidates():
    seen, out = set(), []
    for label, py in (("$HVP_PY", os.environ.get("HVP_PY")), ("current interpreter", sys.executable),
                      ("cache venv", venv_python(cache_venv()))):
        if py and py not in seen and os.path.exists(py):
            seen.add(py)
            out.append((label, py))
    return out


def resolve(strict=False):
    for label, py in candidates():
        if not compare(py, strict):
            return label, py
    return None, None


def project_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else os.getcwd()


def hint(root):
    has_flake = os.path.exists(os.path.join(root, "flake.nix"))
    lines = ["no python with the locked data packages was found."]
    if has_flake:
        lines.append("this project uses a Nix flake: add the devShell from %s/env/flake.nix to it (or merge its "
                     "shellHook), then run tools inside `nix develop -c ...`" % SKILL)
    lines.append("or create the shared venv once: python3 %s setup" % os.path.join(HERE, "hvp_env.py"))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="op", required=True)
    sub.add_parser("detect")
    c = sub.add_parser("check")
    c.add_argument("--strict", action="store_true")
    s = sub.add_parser("setup")
    s.add_argument("--venv")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--print-python", action="store_true")
    sub.add_parser("which")
    r = sub.add_parser("run")
    r.add_argument("tool")
    r.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    if a.op == "detect":
        root = project_root()
        print("PROJECT  %s" % root)
        found = [(f, d) for f, d in MARKERS if os.path.exists(os.path.join(root, f))]
        for f, d in found:
            print("MARKER   %-18s %s" % (f, d))
        if not found:
            print("MARKER   none (no Nix, venv, conda or lock files at the project root)")
        print("IN_NIX   %s" % ("yes (%s)" % os.environ["IN_NIX_SHELL"] if os.environ.get("IN_NIX_SHELL") else "no"))
        print("LOCK     %s (sha %s)" % (os.path.relpath(LOCK, root) if LOCK.startswith(root) else LOCK, lock_hash()))
        label, py = resolve()
        print("DATA_PY  %s" % ("%s  [%s]" % (py, label) if py else "none"))
        if any(f == "flake.nix" for f, _ in found):
            print("ADVICE   project checks keep running as `nix develop -c <cmd>`; for data tools merge "
                  "env/flake.nix into the project flake so both share one shell")
        elif not py:
            print("ADVICE   " + hint(root).replace("\n", "\n         "))
        return 0

    if a.op == "check":
        rc = 0
        for label, py in candidates() or [("none", None)]:
            if py is None:
                print("NO_PYTHON")
                rc = 2
                break
            bad = compare(py, a.strict)
            print("%-8s %s  [%s]" % ("MATCH" if not bad else "MISMATCH", py, label))
            for k, (want, have) in sorted(bad.items()):
                print("         %-22s want %-12s have %s" % (k, want, have))
        label, py = resolve(a.strict)
        if not py:
            print(hint(project_root()))
            return 2
        print("USE      %s  [%s]" % (py, label))
        return rc

    if a.op == "setup":
        d = a.venv or cache_venv()
        py = venv_python(d)
        if py and not compare(py, strict=True):
            if not a.quiet:
                print("EXISTS   %s matches the lock" % d)
            if a.print_python:
                print(py)
            return 0
        os.makedirs(os.path.dirname(d) or ".", exist_ok=True)
        log = sys.stderr if a.quiet else sys.stdout
        print("CREATE   %s" % d, file=log)
        subprocess.run([sys.executable, "-m", "venv", d], check=True)
        py = venv_python(d)
        r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--no-deps",
                            "--only-binary", ":all:", "-r", LOCK], stdout=log, stderr=log)
        if r.returncode != 0:
            print("SETUP_FAILED pip exit %d (wheels for this platform/python may be missing; lock was resolved "
                  "for CPython 3.13)" % r.returncode, file=sys.stderr)
            return 3
        bad = compare(py, strict=True)
        if bad:
            print("SETUP_MISMATCH %s" % bad, file=sys.stderr)
            return 3
        print("READY    %s" % py, file=log)
        if a.print_python:
            print(py)
        return 0

    if a.op == "which":
        label, py = resolve()
        if not py:
            print(hint(project_root()), file=sys.stderr)
            return 2
        print(py)
        return 0

    if a.op == "run":
        label, py = resolve()
        if not py:
            print(hint(project_root()), file=sys.stderr)
            return 2
        tool = a.tool if os.path.isabs(a.tool) else os.path.join(HERE, a.tool)
        os.execv(py, [py, tool] + a.args)
    return 3


if __name__ == "__main__":
    sys.exit(main())

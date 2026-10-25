#!/usr/bin/env python3
"""Known-answer self test for the data layer (MCAP -> Parquet -> DuckDB -> evidence -> MCP).

  python3 hvp_env.py run selftest_data.py [-v]

Builds synthetic MCAP recordings whose answers are known by construction, in a throwaway git
repository, and checks every tool against them:
  r1  current 1.0 + 0.2 sin(2π t) A, temperature 25 -> 35 °C over 2 s
  r2  as r1, plus a current spike: 2.4 A from t = 1.000 to 1.080 s with a 2.5 A peak at 1.040 s;
      temperature 25 -> 45 °C
  r3  as r1, plus a short spike 2.2 A from 1.500 to 1.520 s (shorter than the 50 ms event rule);
      temperature 25 -> 40 °C; one channel in an encoding the tools do not decode
Sampling: motor 1 kHz, temperature 10 Hz. Expected answers are written next to each case.
"""
import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
VERBOSE = "-v" in sys.argv
RESULTS = []


def case(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(detail)[:600]))


def run(tool, args, cwd, env=None):
    p = subprocess.run([PY, os.path.join(HERE, tool)] + args, cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    if VERBOSE:
        print("$", tool, " ".join(args), "->", p.returncode)
        print(p.stdout.rstrip())
        print(p.stderr.rstrip())
    return p.returncode, p.stdout + p.stderr


def sh(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, shell=True, check=True, capture_output=True)


def write_run(path, run_id, temp_end, spike=None, extra_channel=False):
    from mcap.writer import Writer
    with open(path, "wb") as f:
        w = Writer(f)
        w.start("hvp-selftest", library="hvp")
        sid = w.register_schema("Motor", "jsonschema", b'{"type":"object"}')
        tid = w.register_schema("Temp", "jsonschema", b'{"type":"object"}')
        cm = w.register_channel("/drive/motor", "json", sid)
        ct = w.register_channel("/housing/temp", "json", tid)
        if extra_channel:
            cx = w.register_channel("/camera/raw", "protobuf", sid)
            w.add_message(cx, log_time=10 ** 9, data=b"\x00\x01", publish_time=10 ** 9)
        t0 = 10 ** 9
        for i in range(2001):                                # 0.000 .. 2.000 s at 1 kHz
            t = i / 1000.0
            c = 1.0 + 0.2 * math.sin(2 * math.pi * t)
            if spike:
                a, b, level, peak_at, peak = spike
                if a - 1e-9 <= t <= b + 1e-9:
                    c = level
                    if peak_at is not None and abs(t - peak_at) < 1e-9:
                        c = peak
            ns = t0 + i * 1_000_000
            w.add_message(cm, log_time=ns, publish_time=ns,
                          data=json.dumps({"current_A": round(c, 6), "speed_rpm": 1200 + i % 7}).encode())
        for i in range(21):                                  # 10 Hz
            t = i / 10.0
            ns = t0 + i * 100_000_000
            w.add_message(ct, log_time=ns, publish_time=ns,
                          data=json.dumps({"temp_C": round(25 + (temp_end - 25) * t / 2.0, 6)}).encode())
        w.add_metadata("hvp.run", {"run_id": run_id, "scenario": "closing cycle", "seed": str(len(run_id)),
                                   "design_commit": "abc1234"})
        w.finish()


async def mcp_session(repo, calls):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=PY, args=[os.path.join(HERE, "hvp_mcp.py"), "--data", "data"], cwd=repo)
    out = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            out.append(sorted(t.name for t in tools.tools))
            for name, args in calls:
                res = await s.call_tool(name, args)
                txt = "".join(getattr(c, "text", "") for c in res.content)
                out.append((res.isError, txt))
    return out


def main():
    root = tempfile.mkdtemp(prefix="hvp-selftest-data-")
    repo = os.path.join(root, "repo")
    os.makedirs(os.path.join(repo, "data", "raw"))
    os.makedirs(os.path.join(repo, "checks"))
    sh("git init -q && git config user.email t@t && git config user.name t && git config commit.gpgsign false", repo)
    open(os.path.join(repo, ".gitignore"), "w").write("data/raw/\ndata/tables/\n")
    raw = os.path.join(repo, "data", "raw")
    write_run(os.path.join(raw, "r1.mcap"), "r1", 35)
    write_run(os.path.join(raw, "r2.mcap"), "r2", 45, spike=(1.000, 1.080, 2.4, 1.040, 2.5))
    write_run(os.path.join(raw, "r3.mcap"), "r3", 40, spike=(1.500, 1.520, 2.2, None, None), extra_channel=True)

    # ------------------------------------------------ ingest
    rc, out = run("hvp_ingest.py", ["mcap", "data/raw/r1.mcap", "data/raw/r2.mcap", "data/raw/r3.mcap"], repo)
    case("ingest: three runs, two tables, 2001 motor rows and 21 temperature rows each",
         rc == 0 and out.count("TABLE  drive__motor                 rows 2001") == 3
         and out.count("TABLE  housing__temp                rows 21") == 3, out)
    case("ingest: undecoded channel is listed as skipped, not dropped silently",
         "SKIP   /camera/raw: encoding protobuf not decoded" in out, out)
    man1 = json.load(open(os.path.join(repo, "data/manifest.json")))
    run3 = json.load(open(os.path.join(repo, "data/runs/r3.json")))
    case("ingest: run metadata carries the MCAP hvp.run record and the source hash",
         run3["metadata"].get("scenario") == "closing cycle" and len(run3["source_sha256"]) == 64
         and run3["skipped_channels"].get("/camera/raw"), run3)
    run("hvp_ingest.py", ["mcap", "data/raw/r1.mcap", "data/raw/r2.mcap", "data/raw/r3.mcap"], repo)
    man2 = json.load(open(os.path.join(repo, "data/manifest.json")))
    case("ingest: deterministic (re-ingest gives the same manifest fingerprint)",
         man1["fingerprint"] == man2["fingerprint"], (man1["fingerprint"], man2["fingerprint"]))

    # ------------------------------------------------ query: metric
    json.dump({"id": "motor-peak", "kind": "metric", "tables": ["drive__motor"], "question": "physical",
               "part": "drive motor",
               "sql": "SELECT run_id, max(current_A) AS peak_A, arg_max(t, current_A) AS t "
                      "FROM drive__motor GROUP BY run_id ORDER BY run_id",
               "metric": {"name": "peak_current_A", "column": "peak_A", "aggregate": "max",
                          "limit": 2.0, "target": 1.8, "sense": "max", "uncertainty": 0.05},
               "locate": ["run_id", "t"], "scope": "motor current over the closing cycle, all runs",
               "assumptions": ["current sensor calibrated"], "limitations": ["1 kHz sampling misses shorter peaks"],
               "criteria_source": {"peak_current_A": "driver rating (proposed)"}},
              open(os.path.join(repo, "checks/motor_peak.json"), "w"), indent=1)
    rc, out = run("hvp_query.py", ["checks/motor_peak.json"], repo)
    ev = json.load(open(os.path.join(repo, "results/motor-peak.json")))
    w = ev["data"]["where"]
    case("query metric: peak 2.5 A in r2 at t = 1.040 s -> FAIL (limit 2.0)",
         rc == 1 and ev["metrics"]["peak_current_A"]["value"] == 2.5 and w["run_id"] == "r2"
         and abs(w["t"] - 1.04) < 1e-9 and ev["status"] == "FAIL", out)
    per = {r[0]: r[1] for r in ev["data"]["rows"]}
    case("query metric: per-run peaks r1 1.2, r3 2.2", abs(per["r1"] - 1.2) < 1e-6 and abs(per["r3"] - 2.2) < 1e-9, per)
    case("query metric: evidence hashes every Parquet file read and the check file",
         sum(1 for k in ev["inputs"] if k.endswith(".parquet")) == 3 and "checks/motor_peak.json" in ev["inputs"], ev["inputs"])

    # ------------------------------------------------ query: events
    json.dump({"id": "overcurrent", "kind": "events", "table": "drive__motor", "condition": "current_A > 2.0",
               "min_duration_s": 0.05, "peak": "current_A", "max_events": 0, "scope": "overcurrent stretches",
               "assumptions": ["2.0 A trip level"], "limitations": ["sample-span duration"]},
              open(os.path.join(repo, "checks/overcurrent.json"), "w"), indent=1)
    rc, out = run("hvp_query.py", ["checks/overcurrent.json"], repo)
    ev2 = json.load(open(os.path.join(repo, "results/overcurrent.json")))
    rows = ev2["data"]["rows"]
    case("query events: one event (r2, 1.000-1.080 s, span 0.080 s, 81 samples, peak 2.5); r3's 20 ms spike excluded",
         rc == 1 and len(rows) == 1 and rows[0][0] == "r2" and abs(rows[0][1] - 1.0) < 1e-9
         and abs(rows[0][3] - 0.08) < 1e-9 and rows[0][4] == 81 and rows[0][5] == 2.5, rows)
    json.dump(dict(json.load(open(os.path.join(repo, "checks/overcurrent.json"))), min_duration_s=0.01, id="overcurrent-10ms"),
              open(os.path.join(repo, "checks/overcurrent10.json"), "w"))
    rc, out = run("hvp_query.py", ["checks/overcurrent10.json"], repo)
    case("query events: with a 10 ms rule both spikes count (r2 and r3)",
         json.load(open(os.path.join(repo, "results/overcurrent-10ms.json")))["metrics"]["event_count"]["value"] == 2, out)

    # ------------------------------------------------ rerun, stale, cite
    sh("git add -A && git commit -qm data", repo)
    env = {"HVP_PY": PY}
    rc, out = run("hvp_cite.py", ["get", "results/motor-peak.json", "/metrics/peak_current_A/value", "--rerun",
                                  "--inputs", "data/tables", "data/runs"], repo, env)
    case("rerun: hvp_cite --rerun reproduces the data-derived value from its recorded command",
         rc == 0 and "RERUN    REPRODUCED" in out and "SCOPE    \"motor current" in out, out)
    write_run(os.path.join(raw, "r2.mcap"), "r2", 45, spike=(1.000, 1.080, 2.4, 1.040, 2.7))
    run("hvp_ingest.py", ["mcap", "data/raw/r2.mcap"], repo)
    rc, out = run("hvp_stale.py", [], repo)
    case("stale: re-ingesting a changed recording marks the evidence that read it as STALE",
         rc == 1 and "STALE   results/motor-peak.json -> data/tables/drive__motor/run_id=r2/part.parquet" in out, out)
    rc, out = run("hvp_query.py", ["--sql", "SELECT max(current_A) FROM drive__motor WHERE run_id = 'r2'"], repo)
    case("ad-hoc SQL: the reader can rerun a query in the terminal (new peak 2.7)", rc == 0 and "\n2.7" in out, out)

    # ------------------------------------------------ MCP server
    calls = [("list_runs", {}),
             ("query", {"sql": "SELECT run_id, max(temp_C) AS m FROM housing__temp GROUP BY 1 ORDER BY 1"}),
             ("query", {"sql": "DELETE FROM drive__motor"}),
             ("query", {"sql": "SELECT * FROM read_csv('/etc/hostname')"}),
             ("series", {"run_id": "r2", "table_name": "drive__motor", "columns": ["current_A"], "t0": 1.0,
                         "t1": 1.1, "max_points": 20}),
             ("run_check", {"check_path": "checks/overcurrent.json"}),
             ("cite", {"evidence_path": "results/motor-peak.json", "pointer": "/metrics/peak_current_A/value"})]
    try:
        res = asyncio.run(asyncio.wait_for(mcp_session(repo, calls), 120))
    except Exception as e:  # noqa: BLE001
        res = None
        case("mcp: server starts and answers", False, repr(e))
    if res:
        tools = res[0]
        case("mcp: six read-only tools", tools == ["cite", "list_runs", "query", "run_check", "schema", "series"], tools)
        lr = json.loads(res[1][1])
        case("mcp list_runs: three runs with metadata and the data fingerprint",
             [r["run_id"] for r in lr["runs"]] == ["r1", "r2", "r3"] and lr["fingerprint"]
             and lr["runs"][0]["metadata"]["scenario"] == "closing cycle", lr)
        q = json.loads(res[2][1])
        case("mcp query: max temperature per run 35 / 45 / 40 with SQL and a rerun command",
             q["rows"] == [["r1", 35.0], ["r2", 45.0], ["r3", 40.0]] and "hvp_query.py" in q["reproduce"], q)
        case("mcp query: a write statement is refused", res[3][0] and "only a single SELECT" in res[3][1], res[3])
        case("mcp query: reading a file is refused (external access disabled)", res[4][0], res[4])
        s = json.loads(res[5][1])
        case("mcp series: 101 samples in 1.0-1.1 s decimated to every 6th, stated",
             s["samples_in_window"] == 101 and s["decimation"] == "every 6-th sample" and len(s["rows"]) == 17, s)
        rcj = json.loads(res[6][1])
        case("mcp run_check: same single r2 event as the saved evidence",
             len(rcj["rows"]) == 1 and rcj["rows"][0][0] == "r2", rcj)
        case("mcp cite: returns the value's scope and provenance", "SCOPE" in res[7][1] and "COMMAND" in res[7][1], res[7])

    st = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    case("data tools write only data/ and results/ (no other files in the repo)",
         all(l[3:].startswith(("data/", "results/")) for l in st.splitlines()), st)

    # ------------------------------------------------ brief over data evidence
    run("hvp_query.py", ["checks/motor_peak.json"], repo)
    rc, out = run("hvp_brief.py", ["--title", "data", "--out-dir", "brief"], repo)
    md = open(os.path.join(repo, "brief/brief.md")).read()
    rc2, out2 = run("hvp_cite.py", ["check", "brief/brief.md", "--rerun", "--inputs", "data/tables", "data/runs"], repo,
                    {"HVP_PY": PY})
    case("brief: data evidence shows where the value came from (run r2, t 1.04 s) and its SQL",
         "데이터 위치(값이 나온 곳): run_id r2" in md and "t 1.04 `ev:results/motor-peak.json#/data/where/t`" in md
         and "```sql" in md, md[:1500])
    case("brief citation check catches evidence made before the re-ingest (events: peak now 2.7)",
         rc2 == 1 and "NOT_REPRODUCED results/overcurrent.json#/data/where/peak -> 2.7" in out2, out2[-600:])
    run("hvp_query.py", ["checks/overcurrent.json"], repo)
    run("hvp_query.py", ["checks/overcurrent10.json"], repo)
    run("hvp_brief.py", ["--title", "data", "--out-dir", "brief"], repo)
    rc2, out2 = run("hvp_cite.py", ["check", "brief/brief.md", "--rerun", "--inputs", "data/tables", "data/runs"], repo,
                    {"HVP_PY": PY})
    case("after regenerating, the brief passes the citation check with every cited value reproduced",
         rc2 == 0 and "problems=0" in out2 and "reproduced=" in out2, out2[-600:])

    n_ok = sum(ok for _, ok in RESULTS)
    print("\nSELFTEST_DATA %d/%d passed (%s)" % (n_ok, len(RESULTS), root))
    if n_ok == len(RESULTS):
        subprocess.run(["rm", "-rf", root])
    return 0 if n_ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Read-only MCP server over the data layer (step 2), for Claude Code and other MCP clients.

  python3 hvp_env.py run hvp_mcp.py --data data          (stdio transport)

Register in a project's .mcp.json:
  {"mcpServers": {"hvp-data": {"command": "python3",
     "args": ["<skill>/scripts/hvp_env.py", "run", "hvp_mcp.py", "--data", "data"]}}}

Safety: at start the Parquet tables are copied into an in-memory DuckDB, then external access is
disabled and the configuration locked, so a query can read only those tables (no files, no
network). Only single SELECT / WITH statements are accepted; results are row-limited.

Every answer carries what is needed to check it yourself: the SQL, the data fingerprint
(data/manifest.json), the DuckDB version, and a terminal command that reruns it
(hvp_query.py --sql ...). Answers are data, not verdicts: verdicts come from saved checks
(hvp_query.py <check.json>) written as evidence.

Tools
  list_runs()                               runs with metadata, duration, topics; tables; fingerprint
  schema(table)                             columns, types, rows per run
  query(sql, max_rows=200)                  read-only SQL
  series(run_id, table, columns, t0, t1, max_points=500)   a time window, evenly decimated (stated)
  run_check(check_path)                     evaluate a saved check without writing files
  cite(evidence_path, pointer)              the meaning and provenance of one evidence value
"""
import argparse
import glob
import json
import os
import subprocess
import sys

try:
    import duckdb
    from mcp.server.fastmcp import FastMCP
except ImportError as e:  # pragma: no cover
    sys.exit("data packages missing (%s): run through `python3 hvp_env.py run hvp_mcp.py ...`" % e)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hvp_common as C  # noqa: E402
import hvp_query as Q  # noqa: E402


def build(top, data):
    root = os.path.join(top, data)
    con, views = Q.connect(root, load=True)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    man_p = os.path.join(root, "manifest.json")
    man = json.load(open(man_p)) if os.path.exists(man_p) else {}
    runs = {}
    for p in sorted(glob.glob(os.path.join(root, "runs", "*.json"))):
        r = json.load(open(p))
        runs[r["run_id"]] = r
    return con, views, man.get("fingerprint"), runs, root


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    top = C.toplevel()
    con, views, fp, runs, root = build(top, a.data)
    mcp = FastMCP("hvp-data")
    base = {"data": a.data, "fingerprint": fp, "duckdb": duckdb.__version__}

    def repro(sql):
        return 'python3 "<skill>/scripts/hvp_env.py" run hvp_query.py --data %s --sql "%s"' % (a.data, " ".join(sql.split()).replace('"', '\\"'))

    def table(sql, max_rows):
        stmts = con.extract_statements(sql)
        if len(stmts) != 1 or stmts[0].type != duckdb.StatementType.SELECT:
            raise ValueError("only a single SELECT/WITH statement is allowed")
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows + 1)
        return cols, [[Q.jsonable(x) for x in r] for r in rows[:max_rows]], len(rows) > max_rows

    @mcp.tool()
    def list_runs() -> dict:
        """Runs with their metadata (scenario, seed, design commit ...), duration and topics, plus tables."""
        return dict(base, runs=[{"run_id": k, "duration_s": v.get("duration_s"), "metadata": v.get("metadata"),
                                 "source": v.get("source"), "source_sha256": v.get("source_sha256"),
                                 "topics": {t: x["rows"] for t, x in v.get("topics", {}).items()},
                                 "skipped_channels": v.get("skipped_channels")} for k, v in runs.items()],
                    tables=sorted(views))

    @mcp.tool()
    def schema(table_name: str) -> dict:
        """Columns, types and rows per run of one table (view name = topic with '/' -> '__')."""
        if table_name not in views:
            raise ValueError("unknown table %s; have %s" % (table_name, sorted(views)))
        cols = con.execute('DESCRIBE "%s"' % table_name).fetchall()
        per = con.execute('SELECT run_id, count(*) FROM "%s" GROUP BY 1 ORDER BY 1' % table_name).fetchall()
        return dict(base, table=table_name, columns=[{"name": c[0], "type": c[1]} for c in cols],
                    rows_per_run={r[0]: r[1] for r in per})

    @mcp.tool()
    def query(sql: str, max_rows: int = 200) -> dict:
        """Read-only SQL over the tables. Returns columns, rows (limited), the SQL and a rerun command."""
        cols, rows, more = table(sql, max(1, min(max_rows, 5000)))
        return dict(base, sql=" ".join(sql.split()), columns=cols, rows=rows, truncated=more, reproduce=repro(sql))

    @mcp.tool()
    def series(run_id: str, table_name: str, columns: list[str], t0: float = 0.0, t1: float = 1e18,
               max_points: int = 500) -> dict:
        """A time window of chosen columns for one run, evenly decimated to max_points (decimation stated)."""
        if table_name not in views:
            raise ValueError("unknown table %s" % table_name)
        have = {c[0] for c in con.execute('DESCRIBE "%s"' % table_name).fetchall()}
        bad = [c for c in columns if c not in have]
        if bad:
            raise ValueError("unknown columns %s" % bad)
        n = con.execute('SELECT count(*) FROM "%s" WHERE run_id = ? AND t BETWEEN ? AND ?' % table_name,
                        [run_id, t0, t1]).fetchone()[0]
        step = max(1, -(-n // max(1, max_points)))
        cl = ", ".join('"%s"' % c for c in ["t"] + list(columns))
        sql = ('SELECT %s FROM (SELECT %s, row_number() OVER (ORDER BY t) - 1 AS i FROM "%s" '
               "WHERE run_id = '%s' AND t BETWEEN %r AND %r) WHERE i %% %d = 0 ORDER BY t") % (
            cl, cl, table_name, run_id.replace("'", "''"), t0, t1, step)
        cols, rows, _ = table(sql, max_points + 1)
        return dict(base, run_id=run_id, table=table_name, columns=cols, rows=rows, samples_in_window=n,
                    decimation="every %d-th sample" % step if step > 1 else "none", sql=sql, reproduce=repro(sql))

    @mcp.tool()
    def run_check(check_path: str) -> dict:
        """Evaluate a saved check file (metric or events) and return the result without writing evidence."""
        full = os.path.join(top, check_path)
        chk = json.load(open(full))
        sql = Q.events_sql(chk) if chk.get("kind") == "events" else chk["sql"]
        cols, rows, more = table(sql, 500)
        return dict(base, check=check_path, check_sha256=C.sha256_file(full), sql=sql, columns=cols, rows=rows,
                    truncated=more, note="to record a verdict run: hvp_env.py run hvp_query.py %s" % check_path)

    @mcp.tool()
    def cite(evidence_path: str, pointer: str) -> str:
        """Meaning (scope, assumptions, limitations), criteria and provenance of one evidence value."""
        r = subprocess.run([sys.executable, os.path.join(HERE, "hvp_cite.py"), "get", evidence_path, pointer],
                           cwd=top, capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr

    mcp.run()


if __name__ == "__main__":
    main()

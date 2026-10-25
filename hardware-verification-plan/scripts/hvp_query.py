#!/usr/bin/env python3
"""SQL over the Parquet tables as a verification oracle (data layer, step 1).
Run through the data environment:  python3 hvp_env.py run hvp_query.py ...

  hvp_query.py checks/motor_peak.json [--data data] [--out results/motor_peak.json]
        run a saved check and write evidence in the verdict convention
  hvp_query.py --sql "SELECT run_id, max(current_A) FROM drive__motor GROUP BY 1" [--data data]
        ad-hoc query, printed as a table (the same view names as checks; what the MCP server runs)
  hvp_query.py --tables [--data data]       list views, columns and row counts per run

Every topic directory under <data>/tables becomes a view of the same name with a run_id column.

Check file (JSON). Two kinds:
  {"id": "motor-peak", "kind": "metric", "tables": ["drive__motor"],
   "sql": "SELECT run_id, max(current_A) AS peak_A, arg_max(t, current_A) AS t FROM drive__motor GROUP BY run_id",
   "metric": {"name": "peak_current_A", "column": "peak_A", "aggregate": "max",
              "limit": 2.0, "target": 1.8, "sense": "max", "uncertainty": 0.05},
   "locate": ["run_id", "t"],
   "scope": "...", "assumptions": [...], "limitations": [...], "criteria_source": {...},
   "question": "physical", "part": "drive motor"}
     aggregate over the result rows: max | min | mean. The row that gives the value is recorded
     under data.where (with the locate columns), so the reader can open that run at that time.
  {"id": "overcurrent", "kind": "events", "table": "drive__motor", "condition": "current_A > 2.0",
   "min_duration_s": 0.05, "peak": "current_A", "max_events": 0, ...context fields}
     maximal stretches where the condition holds, per run; duration = t_end - t_start of the
     samples inside the stretch (one sample period shorter than the physical duration);
     metric event_count with limit max_events, sense max.

The evidence records the SQL actually run, DuckDB version, sha256 of every Parquet file read and
of the check file (as inputs, so hvp_stale.py sees data changes), the result rows (up to 500),
and a provenance command that reruns it (hvp_cite.py --rerun; pass ignored data with --inputs).
"""
import argparse
import glob
import json
import os
import sys

try:
    import duckdb
except ImportError as e:  # pragma: no cover
    sys.exit("data packages missing (%s): run this tool through `python3 hvp_env.py run hvp_query.py ...`" % e)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def connect(root, load=False):
    """In-memory DuckDB with one view (or, load=True, one table) per topic."""
    con = duckdb.connect(":memory:")
    views = {}
    for d in sorted(glob.glob(os.path.join(root, "tables", "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        files = sorted(glob.glob(os.path.join(d, "run_id=*", "*.parquet")))
        if not files:
            continue
        src = "read_parquet(%s, hive_partitioning=true)" % ("[" + ",".join("'%s'" % f.replace("'", "''") for f in files) + "]")
        con.execute('CREATE %s "%s" AS SELECT * FROM %s' % ("TABLE" if load else "VIEW", name, src))
        views[name] = files
    return con, views


def run_sql(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return cols, [list(r) for r in cur.fetchall()]


def events_sql(chk):
    return (
        "WITH s AS (SELECT run_id, t, ({cond}) AS hit, {peak} AS v FROM \"{tbl}\"),\n"
        "g AS (SELECT *, sum(CASE WHEN hit THEN 0 ELSE 1 END) OVER (PARTITION BY run_id ORDER BY t) AS grp FROM s),\n"
        "e AS (SELECT run_id, grp, min(t) AS t_start, max(t) AS t_end, count(*) AS samples, max(v) AS peak\n"
        "      FROM g WHERE hit GROUP BY run_id, grp)\n"
        "SELECT run_id, t_start, t_end, t_end - t_start AS duration_s, samples, peak FROM e\n"
        "WHERE t_end - t_start >= {mind} ORDER BY run_id, t_start"
    ).format(cond=chk["condition"], peak=chk.get("peak") or "NULL", tbl=chk["table"], mind=float(chk.get("min_duration_s", 0)))


def jsonable(v):
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", nargs="?")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out")
    ap.add_argument("--sql")
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--max-rows", type=int, default=50)
    a = ap.parse_args()
    top = C.toplevel()
    root = os.path.join(top, a.data)
    con, views = connect(root)
    if not views:
        print("NO_TABLES under %s/tables (run hvp_ingest.py first)" % os.path.relpath(root, top))
        return 3

    if a.tables:
        for v in views:
            cols = [r[0] for r in con.execute('DESCRIBE "%s"' % v).fetchall()]
            per = con.execute('SELECT run_id, count(*) FROM "%s" GROUP BY 1 ORDER BY 1' % v).fetchall()
            print("VIEW %-28s columns %s" % (v, ",".join(cols)))
            print("     rows per run %s" % ", ".join("%s:%d" % r for r in per))
        return 0

    if a.sql:
        cols, rows = run_sql(con, a.sql)
        print("SQL  %s" % " ".join(a.sql.split()))
        print("DATA %s (duckdb %s)" % (os.path.relpath(root, top), duckdb.__version__))
        print("\t".join(cols))
        for r in rows[:a.max_rows]:
            print("\t".join("%.6g" % x if isinstance(x, float) else str(x) for x in r))
        if len(rows) > a.max_rows:
            print("... %d more rows (use --max-rows)" % (len(rows) - a.max_rows))
        return 0

    if not a.check:
        ap.error("give a check file, --sql or --tables")
    chk_path = os.path.abspath(a.check)
    chk = json.load(open(chk_path))
    kind = chk.get("kind", "metric")
    if kind == "events":
        sql = events_sql(chk)
        tables = [chk["table"]]
    else:
        sql = chk["sql"]
        tables = chk.get("tables") or list(views)
    missing = [t for t in tables if t not in views]
    if missing:
        print("UNKNOWN_TABLE %s (have: %s)" % (",".join(missing), ",".join(views)))
        return 3
    cols, rows = run_sql(con, sql)
    rows = [[jsonable(x) for x in r] for r in rows]
    where = None
    if kind == "events":
        m = {"name": "event_count", "value": len(rows), "limit": chk.get("max_events", 0), "sense": "max"}
        if rows:
            ix = max(range(len(rows)), key=lambda i: (rows[i][3] or 0))
            where = dict(zip(cols, rows[ix]))
    else:
        spec = chk["metric"]
        ci = cols.index(spec["column"])
        vals = [(r[ci], i) for i, r in enumerate(rows) if C._num(r[ci])]
        if not vals:
            print("NO_VALUES column %s is empty" % spec["column"])
            return 1
        agg = spec.get("aggregate", "max")
        if agg == "mean":
            value, ix = sum(v for v, _ in vals) / len(vals), None
        else:
            value, ix = (max if agg == "max" else min)(vals)
        m = {k: spec[k] for k in ("limit", "target", "sense", "uncertainty") if k in spec}
        m.update(name=spec["name"], value=value)
        if ix is not None:
            loc = chk.get("locate") or cols
            where = {c: rows[ix][cols.index(c)] for c in loc if c in cols}
    name = m.pop("name")
    status = C.metric_status(m) if m.get("limit") is not None else None

    out = a.out or os.path.join("results", "%s.json" % chk.get("id", os.path.splitext(os.path.basename(a.check))[0]))
    inputs = {os.path.relpath(f, top): C.sha256_file(f) for t in tables for f in views[t]}
    inputs[os.path.relpath(chk_path, top)] = C.sha256_file(chk_path)
    rel_check = os.path.relpath(chk_path, top)
    cmd = 'python3 "${HVP_SKILL:-%s}/scripts/hvp_env.py" run hvp_query.py %s --data %s --out %s' % (
        SKILL, rel_check, a.data, out)
    ev = {"status": status, "layer": chk.get("layer", "L4"), "question": chk.get("question"), "part": chk.get("part"),
          "method": "DuckDB SQL over Parquet (%s)" % kind,
          "scope": chk.get("scope"), "assumptions": chk.get("assumptions"), "limitations": chk.get("limitations"),
          "criteria_source": chk.get("criteria_source"),
          "inputs": inputs, "provenance": {"command": cmd},
          "metrics": {name: m},
          "data": {"kind": kind, "sql": sql, "tables": tables, "duckdb": duckdb.__version__, "columns": cols,
                   "rows": rows[:500], "row_count": len(rows), "where": where}}
    ev = {k: v for k, v in ev.items() if v is not None}
    os.makedirs(os.path.dirname(os.path.join(top, out)) or top, exist_ok=True)
    with open(os.path.join(top, out), "w") as f:
        json.dump(ev, f, indent=1, sort_keys=True)
    print("CHECK    %s (%s) -> %s" % (chk.get("id"), kind, out))
    print("METRIC   %s = %s  (limit %s, target %s, sense %s)" % (name, "%.6g" % m["value"], m.get("limit"),
                                                              m.get("target"), m.get("sense", "min")))
    print("STATUS   %s" % status)
    if where:
        print("WHERE    %s" % ", ".join("%s=%s" % (k, "%.6g" % v if isinstance(v, float) else v) for k, v in where.items()))
    print("ROWS     %d (first %d kept in evidence)" % (len(rows), min(len(rows), 500)))
    print("INPUTS   %d parquet file(s) + check file, hashed" % (len(inputs) - 1))
    return 0 if status in (None, "PASS") else 1


if __name__ == "__main__":
    sys.exit(main())

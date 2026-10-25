#!/usr/bin/env python3
"""Recordings -> Parquet tables with run metadata and a hash manifest (data layer, step 1).
Run through the data environment:  python3 hvp_env.py run hvp_ingest.py ...

  hvp_ingest.py mcap data/raw/r12.mcap [more.mcap ...] [--data data]
  hvp_ingest.py csv  rig/2026-11-03.csv --topic bench/motor --run-id bench-03 --time-col t [--time-unit s|ms|us|ns]

Layout under --data (default data/, paths are repository-relative):
  raw/<run>.mcap                       recordings (keep out of git, or use LFS; their hashes go in runs/)
  tables/<topic>/run_id=<run>/part.parquet   one table per topic, hive-partitioned by run
  runs/<run>.json                      run metadata: source file and sha256, the MCAP metadata record
                                       "hvp.run" (scenario, seed, design commit, tool versions ...),
                                       per-topic row counts and fields, channels skipped and why
  manifest.json                        sha256 of every table and run file

Columns: t (seconds from the first message of the run), log_time_ns, then the message fields
flattened with "_" (nested objects) and "_<i>" (arrays up to 16 elements).
MCAP message encodings: json is decoded. ros2/cdr is decoded only if mcap-ros2-support is installed;
otherwise those channels are listed as skipped in runs/<run>.json (never silently dropped).
Ingest is deterministic: the same recording gives byte-identical Parquet.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from mcap.reader import make_reader
except ImportError as e:  # pragma: no cover
    sys.exit("data packages missing (%s): run this tool through `python3 hvp_env.py run hvp_ingest.py ...`" % e)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hvp_common as C  # noqa: E402

MAX_ARRAY = 16


def topic_dir(topic):
    t = topic.strip("/").replace("/", "__")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", t) or "root"


def flatten(obj, pre="", out=None):
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, "%s_%s" % (pre, k) if pre else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:MAX_ARRAY]):
            flatten(v, "%s_%d" % (pre, i), out)
    elif isinstance(obj, (int, float, bool, str)) or obj is None:
        out[re.sub(r"[^A-Za-z0-9_]", "_", pre or "value")] = obj
    return out


def sha(path):
    return C.sha256_file(path)


def write_topic(root, run_id, topic, rows):
    rows.sort(key=lambda r: r["log_time_ns"])
    cols = ["t", "log_time_ns"] + sorted({k for r in rows for k in r} - {"t", "log_time_ns"})
    arrays = {}
    for c in cols:
        vals = [r.get(c) for r in rows]
        try:
            arrays[c] = pa.array(vals)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays[c] = pa.array([None if v is None else str(v) for v in vals])
    table = pa.table(arrays)
    d = os.path.join(root, "tables", topic_dir(topic), "run_id=%s" % run_id)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "part.parquet")
    pq.write_table(table, p, compression="zstd", write_statistics=True)
    return p, len(rows), cols[2:]


def ros2_decoder():
    try:
        from mcap_ros2.decoder import DecoderFactory
        return DecoderFactory()
    except ImportError:
        return None


def ingest_mcap(path, root, top):
    meta, per_topic, skipped = {}, {}, {}
    ros2 = ros2_decoder()
    with open(path, "rb") as f:
        reader = make_reader(f, decoder_factories=[ros2] if ros2 else [])
        for m in reader.iter_metadata():
            if m.name == "hvp.run":
                meta = dict(m.metadata)
        f.seek(0)
        reader = make_reader(f, decoder_factories=[ros2] if ros2 else [])
        t0 = None
        for schema, channel, msg in reader.iter_messages(log_time_order=True):
            enc = channel.message_encoding
            if enc == "json":
                obj = json.loads(msg.data)
            elif enc == "cdr" and ros2:
                dec = ros2.decoder_for(enc, schema)
                o = dec(msg.data)
                obj = {k: getattr(o, k) for k in o.__slots__} if hasattr(o, "__slots__") else {}
            else:
                skipped.setdefault(channel.topic, "encoding %s not decoded (install mcap-ros2-support for cdr)" % enc)
                continue
            t0 = msg.log_time if t0 is None else t0
            row = flatten(obj)
            row["log_time_ns"] = msg.log_time
            per_topic.setdefault(channel.topic, []).append(row)
    run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", meta.get("run_id") or os.path.splitext(os.path.basename(path))[0])
    t0 = min((r["log_time_ns"] for rows in per_topic.values() for r in rows), default=0)
    tmax = max((r["log_time_ns"] for rows in per_topic.values() for r in rows), default=0)
    topics = {}
    for topic, rows in sorted(per_topic.items()):
        for r in rows:
            r["t"] = (r["log_time_ns"] - t0) / 1e9
        p, n, fields = write_topic(root, run_id, topic, rows)
        topics[topic] = {"table": topic_dir(topic), "rows": n, "fields": fields, "file": os.path.relpath(p, top)}
    return run_id, {"run_id": run_id, "source": os.path.relpath(os.path.abspath(path), top), "source_sha256": sha(path),
                    "metadata": meta, "duration_s": (tmax - t0) / 1e9, "topics": topics, "skipped_channels": skipped}


def ingest_csv(path, root, top, topic, run_id, tcol, unit):
    scale = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0}[unit]
    rows = []
    with open(path, newline="") as f:
        for rec in csv.DictReader(f):
            row = {}
            for k, v in rec.items():
                key = re.sub(r"[^A-Za-z0-9_]", "_", k.strip())
                try:
                    row[key] = float(v) if v not in ("", None) else None
                except ValueError:
                    row[key] = v
            if row.get(tcol) is None:
                continue
            row["log_time_ns"] = int(round(row.pop(tcol) * scale))
            rows.append(row)
    t0 = min((r["log_time_ns"] for r in rows), default=0)
    for r in rows:
        r["t"] = (r["log_time_ns"] - t0) / 1e9
    p, n, fields = write_topic(root, run_id, topic, rows)
    dur = (max(r["log_time_ns"] for r in rows) - t0) / 1e9 if rows else 0.0
    return run_id, {"run_id": run_id, "source": os.path.relpath(os.path.abspath(path), top), "source_sha256": sha(path),
                    "metadata": {"source_format": "csv", "time_col": tcol, "time_unit": unit}, "duration_s": dur,
                    "topics": {topic: {"table": topic_dir(topic), "rows": n, "fields": fields,
                                       "file": os.path.relpath(p, top)}}, "skipped_channels": {}}


def merge_run(root, run_id, info):
    p = os.path.join(root, "runs", "%s.json" % run_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):                       # a CSV adds a topic to an existing run
        old = json.load(open(p))
        if old.get("source") != info["source"]:
            old.setdefault("extra_sources", {})[info["source"]] = info["source_sha256"]
            old["topics"].update(info["topics"])
            info = old
    with open(p, "w") as f:
        json.dump(info, f, indent=1, sort_keys=True)


def write_manifest(root, top):
    files = {}
    for sub in ("tables", "runs"):
        for d, _, fs in os.walk(os.path.join(root, sub)):
            for fn in fs:
                p = os.path.join(d, fn)
                files[os.path.relpath(p, top)] = sha(p)
    m = {"files": dict(sorted(files.items())),
         "fingerprint": hashlib.sha256("\n".join("%s %s" % kv for kv in sorted(files.items())).encode()).hexdigest()}
    with open(os.path.join(root, "manifest.json"), "w") as f:
        json.dump(m, f, indent=1)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="op", required=True)
    m = sub.add_parser("mcap")
    m.add_argument("files", nargs="+")
    c = sub.add_parser("csv")
    c.add_argument("file")
    c.add_argument("--topic", required=True)
    c.add_argument("--run-id", required=True)
    c.add_argument("--time-col", default="t")
    c.add_argument("--time-unit", default="s", choices=["s", "ms", "us", "ns"])
    for p in (m, c):
        p.add_argument("--data", default="data")
    a = ap.parse_args()
    top = C.toplevel()
    root = os.path.join(top, a.data)
    done = []
    if a.op == "mcap":
        for f in a.files:
            run_id, info = ingest_mcap(f, root, top)
            merge_run(root, run_id, info)
            done.append((run_id, info))
    else:
        run_id, info = ingest_csv(a.file, root, top, a.topic, a.run_id, a.time_col, a.time_unit)
        merge_run(root, run_id, info)
        done.append((run_id, info))
    man = write_manifest(root, top)
    for run_id, info in done:
        print("RUN      %s  source %s  sha %s  duration %.6g s" % (run_id, info["source"], info["source_sha256"][:12],
                                                                 info["duration_s"]))
        for topic, t in info["topics"].items():
            print("  TABLE  %-28s rows %-8d fields %s" % (t["table"], t["rows"], ",".join(t["fields"])[:100]))
        for topic, why in info["skipped_channels"].items():
            print("  SKIP   %s: %s" % (topic, why))
    print("MANIFEST %s  files %d  fingerprint %s" % (os.path.relpath(os.path.join(root, "manifest.json"), top),
                                                   len(man["files"]), man["fingerprint"][:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Convert the downloaded dump files into one CSV per table.

Reads the files fetchdata.py wrote into ./dumps (NDJSON, gzip-compressed
despite the .jsonl name) and writes ./csv/<table>.csv.

CSV is flat, so nested values are expanded:
  dict            -> one column per key, named <field>__<key>
                     (items.item_state -> item_state__gender, ...)
  [{...}]         -> same, for a single-element list of dicts
                     (items.item_locations -> item_locations__lat/__lng)
  ["a","b"]       -> joined with "|"          (user.domains)
  null / empty    -> empty cell
  anything longer -> compact JSON, so nothing is silently dropped

Columns are the union across every record (the payloads are sparse), in
first-seen order. Written UTF-8 with a BOM so Excel reads the non-ASCII
values correctly.

Usage:
  python to_csv.py                     # dumps/ -> csv/
  python to_csv.py --in-dir d --out-dir c
"""

import argparse
import csv
import gzip
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TABLES = ["user", "items", "item_actions"]
GZIP_MAGIC = b"\x1f\x8b"


def open_maybe_gzip(path):
    """The dump files are gzip but named .jsonl, so sniff rather than trust."""
    with open(path, "rb") as probe:
        packed = probe.read(2) == GZIP_MAGIC
    opener = gzip.open if packed else open
    return opener(path, "rt", encoding="utf-8")


def read_records(path):
    with open_maybe_gzip(path) as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name} line {lineno}: bad JSON: {exc}")


def as_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def flatten(record):
    """One nesting level out into columns; see the module docstring."""
    row = {}
    for key, value in record.items():
        if isinstance(value, dict) and value:
            for sub, sub_value in value.items():
                row[f"{key}__{sub}"] = as_cell(sub_value)
        elif isinstance(value, list) and value:
            if len(value) == 1 and isinstance(value[0], dict):
                for sub, sub_value in value[0].items():
                    row[f"{key}__{sub}"] = as_cell(sub_value)
            elif any(isinstance(item, (dict, list)) for item in value):
                row[key] = as_cell(value)
            else:
                row[key] = "|".join("" if v is None else str(v) for v in value)
        else:
            # None, empty dict and empty list all land here as an empty cell.
            row[key] = as_cell(value)
    return row


def convert(src, dest):
    rows = [flatten(record) for record in read_records(src)]

    columns = {}
    for row in rows:
        for key in row:
            columns.setdefault(key, None)

    # newline="" keeps the csv module from doubling \r on Windows.
    with open(dest, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), restval="")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), len(columns)


def main():
    parser = argparse.ArgumentParser(description="Convert the dump files to CSV.")
    parser.add_argument("--in-dir", default=SCRIPT_DIR / "dumps")
    parser.add_argument("--out-dir", default=SCRIPT_DIR / "csv")
    args = parser.parse_args()

    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    missing = [t for t in TABLES if not (in_dir / f"{t}.jsonl").exists()]
    if missing:
        raise SystemExit(
            f"ERROR: no dump for {', '.join(missing)} in {in_dir} — run fetchdata.py first."
        )

    for table in TABLES:
        dest = out_dir / f"{table}.csv"
        count, width = convert(in_dir / f"{table}.jsonl", dest)
        print(f"  {table}: {count:,} rows x {width} cols -> {dest}")

    print(f"\nDone. CSVs in {out_dir}")


if __name__ == "__main__":
    main()

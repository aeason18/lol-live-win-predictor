"""Load the prepared training split into the Supabase `matches` table.

    py prep/prepare.py                         # first: makes data/training/lol_15min_live_train.csv
    py dashboard/upload_to_supabase.py
    py dashboard/upload_to_supabase.py --dry-run   # check the rows without connecting

Needs two environment variables (the service-role key can write; never put it in the dashboard):
    SUPABASE_URL           https://<project>.supabase.co
    SUPABASE_SERVICE_KEY   the service_role key from Project Settings > API

Safe to re-run: rows are upserted on match_id. Only the training split goes up. Player ids, champions and
the held-out test matches stay local.
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TRAINING_DIR = ROOT / "data" / "training"

COLUMNS = ["match_id", "rank_tier", "patch", "start_time", "game_duration_s", "blue_win", "blue_first_blood",
           "level_sum_diff", "cs_diff", "kills_diff", "assists_diff", "towers_diff", "dragons_diff",
           "voidgrubs_diff"]
BATCH = 500


def build_rows(path):
    if "HOLDOUT" in Path(path).name.upper():
        sys.exit("Refusing to upload the held-out test set.")
    df = pd.read_csv(path, dtype={"patch": str})
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        sys.exit(f"{path} is missing columns: {missing}")

    holdout = TRAINING_DIR / "lol_15min_live_test_HOLDOUT.csv"
    if holdout.exists():
        overlap = set(df.match_id) & set(pd.read_csv(holdout, usecols=["match_id"]).match_id)
        if overlap:
            sys.exit(f"{len(overlap)} matches in this file are also in the held-out test set.")

    df = df[COLUMNS].copy()
    if df.isna().any().any() or df.match_id.duplicated().any():
        sys.exit("Found nulls or duplicate match ids; rerun prep/prepare.py.")
    df["start_time"] = pd.to_datetime(df.start_time).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return df.to_dict("records")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=TRAINING_DIR / "lol_15min_live_train.csv")
    ap.add_argument("--table", default="matches")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = build_rows(args.src)
    print(f"{len(rows):,} rows ready from {args.src.name}")
    if args.dry_run:
        print("first row:", rows[0])
        return

    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY first.")

    from supabase import create_client
    client = create_client(url, key)
    for i in range(0, len(rows), BATCH):
        client.table(args.table).upsert(rows[i:i + BATCH], on_conflict="match_id").execute()
        print(f"  uploaded {min(i + BATCH, len(rows)):,} / {len(rows):,}")

    count = client.table(args.table).select("match_id", count="exact").limit(1).execute().count
    print(f"table now holds {count:,} rows")
    if count != len(rows):
        print("note: the table holds a different number of rows than this file; it may contain older uploads.")


if __name__ == "__main__":
    main()

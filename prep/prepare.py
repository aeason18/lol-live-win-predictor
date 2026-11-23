"""Prepare the 15-minute match data using only stats the live game API provides.

    py prep/prepare.py                  # reads data/training/lol_15min_all.csv, writes the live train/test files
    py prep/prepare.py --out some/dir   # write somewhere else (nothing in data/training is touched)

The steps are the ones in notebooks/lol_15min_live_audit_and_split.ipynb, which explains why each
one is there:

    1. load           patch is read as text ("16.20" would otherwise become 16.2)
    2. drop_non_live  gold, XP, plates, wards and per-role gold are not available in a live game
    3. clean          fix patch strings, drop games that ended before the 15:00 snapshot, sort by time
    4. add_features   game_minutes, live-style CS (the live API only updates CS in steps of 10)
    5. split          20% held-out test set, stratified by rank and outcome, fixed seed

The held-out matches are the same ones the full-feature notebook reserves; the SHA-256 fingerprint in
data/training/lol_15min_test_HOLDOUT.sha256 is checked so a different split can't slip through.
"""

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TRAINING_DIR = ROOT / "data" / "training"

SEED = 42
TEST_FRAC = 0.20
ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
RANKS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER_PLUS"]
MIN_GAME_SECONDS = 900  # a "15:00" snapshot from a game that ended earlier isn't a 15:00 state

NOT_LIVE_STATS = ["gold", "xp", "plates", "wards"]
# Predictors usable in a live game. Heralds are left out: they are always 0 at 15:00.
LIVE_FEATURES = ["level_sum_diff", "cs_diff", "kills_diff", "assists_diff", "towers_diff",
                 "dragons_diff", "voidgrubs_diff", "blue_first_blood"]


def load(path):
    return pd.read_csv(path, dtype={"patch": str})


def drop_non_live(df):
    """Remove every column the live API cannot provide (team and per-role gold, XP, plates, wards)."""
    cols = (
        [f"{side}_{s}" for side in ("blue", "red") for s in NOT_LIVE_STATS]
        + [f"{s}_diff" for s in NOT_LIVE_STATS]
        + [f"{side}_{r}_gold" for side in ("blue", "red") for r in ROLES]
        + [f"{r.lower()}_gold_diff" for r in ROLES]
    )
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"expected columns not found: {missing[:5]}")
    return df.drop(columns=cols)


def clean(df):
    """Return (clean frame, log of what changed). Never imputes or removes outliers: nothing is missing,
    and big leads are real games."""
    log = []
    df = df.copy()
    df["start_time"] = pd.to_datetime(df.game_start_ms, unit="ms")

    # "16.2" is patch 16.20 whose trailing zero was lost
    df["patch"] = df.patch.where(df.patch.str.split(".").str[1].str.len() == 2, df.patch + "0")

    short = df.game_duration_s < MIN_GAME_SECONDS
    log.append(f"dropped {int(short.sum())} games that ended before 15:00")
    df = df[~short].sort_values("game_start_ms").reset_index(drop=True)

    df["rank_tier"] = pd.Categorical(df.rank_tier, categories=RANKS, ordered=True)
    df["blue_win"] = df.blue_win.astype("int8")
    df["blue_first_blood"] = df.blue_first_blood.astype("int8")
    return df, log


def add_features(df, seed=SEED):
    """game_minutes, n_champs_unique and cs_diff_live_sim.

    The live API rounds each player's CS down to a multiple of 10, so a live reading is up to 9 below the
    truth per player. The data holds team totals only, so the loss is simulated: 0-9 per player, 5 a side."""
    df = df.copy()
    df["game_minutes"] = df.game_duration_s / 60
    df["n_champs_unique"] = df[[f"{s}_{r}_champ" for s in ("blue", "red") for r in ROLES]].nunique(axis=1)
    rng = np.random.default_rng(seed + 1)
    lost_blue = rng.integers(0, 10, (len(df), 5)).sum(axis=1)
    lost_red = rng.integers(0, 10, (len(df), 5)).sum(axis=1)
    df["cs_diff_live_sim"] = df.cs_diff - lost_blue + lost_red
    return df


def split(df, test_frac=TEST_FRAC, seed=SEED):
    """(train, test), stratified by rank_tier x blue_win."""
    strata = df.rank_tier.astype(str) + "_" + df.blue_win.astype(str)
    rng = np.random.default_rng(seed)
    test_idx = []
    for _, idx in df.groupby(strata, observed=True).groups.items():
        idx = np.array(idx)
        test_idx.extend(rng.choice(idx, size=int(round(len(idx) * test_frac)), replace=False))
    is_test = df.index.isin(test_idx)
    return df[~is_test].copy(), df[is_test].copy()


def fingerprint(test):
    return hashlib.sha256("\n".join(sorted(test.match_id)).encode()).hexdigest()


def prepare(src):
    """Run every step. Returns (train, test, notes)."""
    raw = load(src)
    df = drop_non_live(raw)
    notes = [f"loaded {len(raw):,} rows x {raw.shape[1]} columns, kept {df.shape[1]} live columns"]
    df, log = clean(df)
    notes += log
    df = add_features(df)
    train, test = split(df)
    notes.append(f"train {len(train):,} / test {len(test):,} matches")
    return train, test, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=TRAINING_DIR / "lol_15min_all.csv")
    ap.add_argument("--out", type=Path, default=TRAINING_DIR)
    args = ap.parse_args()

    train, test, notes = prepare(args.src)
    print("\n".join(notes))

    expected_file = TRAINING_DIR / "lol_15min_test_HOLDOUT.sha256"
    got = fingerprint(test)
    if expected_file.exists():
        if expected_file.read_text().strip() != got:
            sys.exit("The held-out matches differ from the saved fingerprint; refusing to write files.")
        print("held-out matches match the saved fingerprint")

    args.out.mkdir(parents=True, exist_ok=True)
    train.to_csv(args.out / "lol_15min_live_train.csv", index=False)
    test.to_csv(args.out / "lol_15min_live_test_HOLDOUT.csv", index=False)
    print(f"wrote lol_15min_live_train.csv and lol_15min_live_test_HOLDOUT.csv to {args.out}")


if __name__ == "__main__":
    main()

"""Fit the D4 pipeline (TeamDiffTransformer -> StandardScaler -> LogisticRegression)
on data/training/lol_15min_live_train.csv and save it as api/model_bundle.joblib.

Never reads lol_15min_live_test_HOLDOUT.csv - that file is reserved for D6.

Run from the repo root with the api/ venv:
    api/.venv/Scripts/python api/train_pipeline.py
"""

import datetime as dt
import platform
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transformers import OUTPUT_FEATURES, REQUIRED_RAW_COLUMNS, TeamDiffTransformer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRAIN_CSV = ROOT / "data" / "training" / "lol_15min_live_train.csv"
BUNDLE_PATH = Path(__file__).resolve().parent / "model_bundle.joblib"

SEED = 42
N_FOLDS = 5
LOGREG_C = 0.1


def build_pipeline():
    return Pipeline([
        ("team_diff", TeamDiffTransformer()),
        ("scaler", StandardScaler()),
        ("logreg", LogisticRegression(C=LOGREG_C, max_iter=2000)),
    ])


def main():
    df = pd.read_csv(TRAIN_CSV, dtype={"patch": str})
    X = df[REQUIRED_RAW_COLUMNS]
    y = df["blue_win"].to_numpy()

    cv = StratifiedKFold(N_FOLDS, shuffle=True, random_state=SEED)
    oof_proba = cross_val_predict(build_pipeline(), X, y, cv=cv, method="predict_proba", n_jobs=1)[:, 1]
    cv_log_loss = log_loss(y, oof_proba)
    cv_auc = roc_auc_score(y, oof_proba)
    print(f"5-fold stratified CV (out-of-fold): log loss {cv_log_loss:.4f}, AUC {cv_auc:.4f}")

    pipeline = build_pipeline().fit(X, y)
    logreg = pipeline.named_steps["logreg"]
    print("Fitted coefficients (standardised scale):")
    for feat, coef in zip(OUTPUT_FEATURES, logreg.coef_[0]):
        print(f"  {feat:<16} {coef:+.4f}")
    print(f"  intercept        {logreg.intercept_[0]:+.4f}")

    bundle = {
        "pipeline": pipeline,
        "metadata": {
            "features": OUTPUT_FEATURES,
            "raw_input_columns": REQUIRED_RAW_COLUMNS,
            "sklearn_version": sklearn.__version__,
            "python_version": platform.python_version(),
            "n_training_rows": len(df),
            "train_date": dt.date.today().isoformat(),
            "cv_metrics": {
                "method": f"{N_FOLDS}-fold stratified cross-validation, seed={SEED}",
                "log_loss": cv_log_loss,
                "auc": cv_auc,
            },
            "params": {"logreg__C": LOGREG_C, "logreg__max_iter": 2000},
        },
    }
    joblib.dump(bundle, BUNDLE_PATH)
    print(f"Saved bundle to {BUNDLE_PATH}")


if __name__ == "__main__":
    main()

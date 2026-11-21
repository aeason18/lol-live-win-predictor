"""Custom transformer: raw per-team counts -> the 8 live diff features.

Mirrors the diffing done by lol_snapshots.py / the D3 notebooks, so the same
raw columns a live game snapshot (or a training CSV) provides can be fed
straight into the pipeline without precomputing *_diff columns by hand.
"""

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

STATS = ["level_sum", "cs", "kills", "assists", "towers", "dragons", "voidgrubs"]
REQUIRED_RAW_COLUMNS = [f"{side}_{s}" for s in STATS for side in ("blue", "red")] + ["blue_first_blood"]
OUTPUT_FEATURES = [f"{s}_diff" for s in STATS] + ["blue_first_blood"]


class TeamDiffTransformer(BaseEstimator, TransformerMixin):
    """Turns blue_/red_ team counts into blue-minus-red diffs, plus blue_first_blood.

    blue_first_blood is passed through as-is; a missing value becomes 0.5
    (neutral - nobody has first blood yet), the same convention lol_snapshots.py
    uses for the live overlay.
    """

    def fit(self, X, y=None):
        X = pd.DataFrame(X)
        missing = [c for c in REQUIRED_RAW_COLUMNS if c not in X.columns]
        if missing:
            raise ValueError(f"TeamDiffTransformer is missing required columns: {missing}")
        self.feature_names_in_ = np.array(REQUIRED_RAW_COLUMNS)
        self.n_features_in_ = len(REQUIRED_RAW_COLUMNS)
        self.feature_names_out_ = np.array(OUTPUT_FEATURES)
        return self

    def transform(self, X):
        X = pd.DataFrame(X)
        missing = [c for c in REQUIRED_RAW_COLUMNS if c not in X.columns]
        if missing:
            raise ValueError(f"TeamDiffTransformer is missing required columns: {missing}")
        out = pd.DataFrame(index=X.index)
        for s in STATS:
            out[f"{s}_diff"] = X[f"blue_{s}"].astype(float) - X[f"red_{s}"].astype(float)
        out["blue_first_blood"] = X["blue_first_blood"].astype(float).fillna(0.5)
        return out[OUTPUT_FEATURES].to_numpy()

    def get_feature_names_out(self, input_features=None):
        return np.array(OUTPUT_FEATURES)

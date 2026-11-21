"""FastAPI service for the D4 win-probability pipeline.

Loads api/model_bundle.joblib once at startup (no retraining here - that's
train_pipeline.py's job) and serves it behind /predict. If loading fails, the
server still starts, but the model-dependent endpoints answer 503 instead of
crashing the whole process.

Run from the repo root with the api/ venv:
    api/.venv/Scripts/python -m uvicorn api.app:app --reload
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transformers import OUTPUT_FEATURES, TeamDiffTransformer  # noqa: E402,F401 (needed to unpickle the bundle)

BUNDLE_PATH = Path(__file__).resolve().parent / "model_bundle.joblib"

# Generous but sane caps: a 15-minute team total realistically never gets near these.
MAX_LEVEL_SUM = 90   # 5 champions, level 18 cap each
MAX_CS = 500
MAX_KILLS = 50
MAX_ASSISTS = 100
MAX_TOWERS = 11
MAX_DRAGONS = 2       # at most 2 dragons can spawn by 15:00
MAX_VOIDGRUBS = 3


class TeamCounts(BaseModel):
    blue_level_sum: int = Field(ge=0, le=MAX_LEVEL_SUM)
    red_level_sum: int = Field(ge=0, le=MAX_LEVEL_SUM)
    blue_cs: int = Field(ge=0, le=MAX_CS)
    red_cs: int = Field(ge=0, le=MAX_CS)
    blue_kills: int = Field(ge=0, le=MAX_KILLS)
    red_kills: int = Field(ge=0, le=MAX_KILLS)
    blue_assists: int = Field(ge=0, le=MAX_ASSISTS)
    red_assists: int = Field(ge=0, le=MAX_ASSISTS)
    blue_towers: int = Field(ge=0, le=MAX_TOWERS)
    red_towers: int = Field(ge=0, le=MAX_TOWERS)
    blue_dragons: int = Field(ge=0, le=MAX_DRAGONS)
    red_dragons: int = Field(ge=0, le=MAX_DRAGONS)
    blue_voidgrubs: int = Field(ge=0, le=MAX_VOIDGRUBS)
    red_voidgrubs: int = Field(ge=0, le=MAX_VOIDGRUBS)
    blue_first_blood: Optional[int] = Field(default=None, ge=0, le=1)

    model_config = {
        "json_schema_extra": {
            "example": {
                "blue_level_sum": 52, "red_level_sum": 49,
                "blue_cs": 310, "red_cs": 275,
                "blue_kills": 5, "red_kills": 3,
                "blue_assists": 9, "red_assists": 6,
                "blue_towers": 2, "red_towers": 0,
                "blue_dragons": 1, "red_dragons": 0,
                "blue_voidgrubs": 2, "red_voidgrubs": 1,
                "blue_first_blood": 1,
            }
        }
    }


class PredictResponse(BaseModel):
    blue_win_probability: float
    red_win_probability: float
    diff_features: dict


model_state = {"pipeline": None, "metadata": None, "error": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        bundle = joblib.load(BUNDLE_PATH)
        model_state["pipeline"] = bundle["pipeline"]
        model_state["metadata"] = bundle["metadata"]
        model_state["error"] = None
    except Exception as exc:  # noqa: BLE001 - any load failure should keep the server up
        model_state["pipeline"] = None
        model_state["metadata"] = None
        model_state["error"] = str(exc)
    yield


app = FastAPI(title="LoL 15-minute win predictor", lifespan=lifespan)


def model_loaded() -> bool:
    return model_state["pipeline"] is not None


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
def health():
    return {"status": "ok" if model_loaded() else "degraded", "model_loaded": model_loaded()}


@app.get("/pipeline-info")
def pipeline_info():
    if not model_loaded():
        raise HTTPException(status_code=503, detail=f"Model unavailable: {model_state['error']}")
    pipeline = model_state["pipeline"]
    return {
        "metadata": model_state["metadata"],
        "steps": [name for name, _ in pipeline.steps],
    }


@app.post("/predict", response_model=PredictResponse)
def predict(team_counts: TeamCounts):
    if not model_loaded():
        raise HTTPException(status_code=503, detail=f"Model unavailable: {model_state['error']}")
    pipeline = model_state["pipeline"]
    row = pd.DataFrame([team_counts.model_dump()])
    proba_blue = float(pipeline.predict_proba(row)[0, 1])
    diffs = pipeline.named_steps["team_diff"].transform(row)[0]
    diff_features = dict(zip(OUTPUT_FEATURES, (float(v) for v in diffs)))
    return PredictResponse(
        blue_win_probability=round(proba_blue, 6),
        red_win_probability=round(1.0 - proba_blue, 6),
        diff_features=diff_features,
    )

# Local vs deployed /predict comparison

Local: `http://127.0.0.1:8000` (uvicorn, api/.venv)
Deployed: https://dvnzil--lol-win-predictor-fastapi-app.us-east.modal.run

`/predict` rounds `blue_win_probability` and `red_win_probability` to 6 decimals, so every row below is an exact match.

## Scenario 1: blue ahead

Input: `blue_level_sum=55, red_level_sum=47, blue_cs=340, red_cs=260, blue_kills=7, red_kills=1, blue_assists=12, red_assists=2, blue_towers=3, red_towers=0, blue_dragons=1, red_dragons=0, blue_voidgrubs=2, red_voidgrubs=0, blue_first_blood=1`

| | blue_win_probability | red_win_probability |
|---|---|---|
| local | 0.95914 | 0.04086 |
| deployed | 0.95914 | 0.04086 |

Match: **exact**

## Scenario 2: red ahead

Input: `blue_level_sum=46, red_level_sum=54, blue_cs=255, red_cs=330, blue_kills=2, red_kills=8, blue_assists=3, red_assists=13, blue_towers=0, red_towers=2, blue_dragons=0, red_dragons=1, blue_voidgrubs=0, red_voidgrubs=3, blue_first_blood=0`

| | blue_win_probability | red_win_probability |
|---|---|---|
| local | 0.04851 | 0.95149 |
| deployed | 0.04851 | 0.95149 |

Match: **exact**

## Scenario 3: even game, no first blood yet

Input: `blue_level_sum=50, red_level_sum=50, blue_cs=300, red_cs=300, blue_kills=2, red_kills=2, blue_assists=4, red_assists=4, blue_towers=1, red_towers=1, blue_dragons=1, red_dragons=1, blue_voidgrubs=1, red_voidgrubs=1, blue_first_blood=null`

| | blue_win_probability | red_win_probability |
|---|---|---|
| local | 0.498796 | 0.501204 |
| deployed | 0.498796 | 0.501204 |

Match: **exact**. `blue_first_blood=null` was correctly defaulted to 0.5 by `TeamDiffTransformer` on both.

## Note on rounding

Before rounding was added, local and deployed `predict_proba` outputs for these same scenarios agreed to about 15 significant digits (e.g. scenario 2: `0.048509717651842144` local vs `0.048509717651842185` deployed) - they only diverged in the last 1-2 digits of a double. That tiny tail comes from local Windows and the deployed Linux container using different BLAS builds for the underlying linear algebra, not from any difference in the pipeline or its inputs. Rounding both outputs to 6 decimals in `/predict` removes that noise entirely, so local and deployed responses now match exactly.

## Other deployed checks

- `GET /health` -> 200, `{"status": "ok", "model_loaded": true}`
- `GET /pipeline-info` -> 200, metadata matches `train_pipeline.py`'s output: `sklearn_version 1.9.1`, `python_version 3.13.5`, `n_training_rows 6527`, `cv_metrics {log_loss: 0.4976, auc: 0.8355}`, steps `["team_diff", "scaler", "logreg"]`
- `POST /predict` with `blue_kills=-5` -> 422, `{"detail": [{"type": "greater_than_equal", "loc": ["body", "blue_kills"], "msg": "Input should be greater than or equal to 0", ...}]}`
- 503 was intentionally **not** tested against the deployed app, per instructions.

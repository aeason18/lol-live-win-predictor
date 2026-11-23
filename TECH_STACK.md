# Tech stack

The whole project is Python. It has four parts:

1. **Desktop overlay:** the Windows app players run during a game.
2. **Data and modelling:** offline scripts and notebooks that collect matches, learn builds and train the model.
3. **Prediction API:** the 15-minute model as a fitted scikit-learn pipeline behind FastAPI, hosted on Modal.
4. **Dashboard:** a web view of the training data (Supabase + Streamlit + Modal).

The overlay has no web framework, no JavaScript and no database. It's local Python talking to Riot's
local and public APIs. Only the API and the dashboard use cloud services.

## 1. Desktop overlay

| Layer | Tech | Files |
|---|---|---|
| Language | Python 3 | all `lol_*.py` |
| UI | **Tkinter**: the always-on-top gem, the next-item box and the champion-select lookup window | `lol_overlay.py`, `lol_itembox.py`, `lol_lookup.py` |
| Graphics | **Pillow**: pixel-art sprites, pixelated item icons, bitmap fonts (Jacquard12, Romulus) | `assets/` |
| Windows integration | **ctypes** (user32/kernel32) for click-through windows and a single-instance mutex; **winreg** for "Start with Windows"; **pystray** for the tray icon | `lol_overlay.py`, `lol_startup.py`, `lol_tray.py` |
| Live game data | **requests** polling Riot's Live Client Data API at `https://127.0.0.1:2999/liveclientdata/allgamedata` | `lol_snapshots.py` |
| Champion-select data | The League client's local API (LCU). The port and auth token come from the client's `lockfile`. Read-only, e.g. `/lol-gameflow/v1/gameflow-phase` | `lol_lcu.py` |
| Items and runes | Riot's **Data Dragon** CDN, downloaded once and cached | `lol_items.py`, `lol_runes.py` |
| Win model at runtime | No ML library: logistic regression coefficients stored as JSON and applied in plain Python | `win_model.json` |
| Build suggestions | Learned item, rune and matchup data | `data/builds.json` |
| Packaging | **PyInstaller**: one-file, no-console `.exe` with the assets, model and builds bundled inside | `build_exe.ps1`, `paths.py` |

`paths.py` separates `RESOURCE_DIR` (bundled files, inside the exe when frozen) from `USER_DIR`
(`%LOCALAPPDATA%\LoLWinOverlay`: settings and `overlay.log`).

```
League game ──► Live Client Data API (127.0.0.1:2999) ──► lol_snapshots.py ──► win_model.json ──► overlay gem
League client ─► LCU API (lockfile) ──────────────────► lol_lcu.py ──► lol_lookup.py (champion select)
Data Dragon ───► lol_items.py + data/builds.json ─────► lol_itembox.py (next-item box)
```

## 2. Data and modelling (offline)

- **Build data:** `build_data.py` collects Emerald+ ranked games from the **Riot Match API** (key in a local
  `.env`). It learns the best item for each build slot, plus rune pages and lane matchups, and writes
  `data/builds.json`. Runs can be stopped and resumed, and `--aggregate-only` re-aggregates games
  already collected.
- **Notebooks (Jupyter):** **pandas**, **numpy**, **scipy**, **matplotlib** and **scikit-learn**.
  - Candidate models: `LogisticRegression`, polynomial features + logistic regression,
    `RandomForestClassifier` and `HistGradientBoostingClassifier`, compared against `DummyClassifier` baselines.
  - Evaluation: nested `StratifiedKFold` with `GridSearchCV`, scored by ROC AUC, plus error analysis by group.
  - Held-out test set: 20%, stratified by rank and outcome. Its SHA-256 fingerprint
    (`data/training/lol_15min_test_HOLDOUT.sha256`) shows it was never changed.
- **Export:** the chosen model's coefficients go to `win_model.json`, so the shipped app doesn't need scikit-learn.

## 3. Prediction API (`api/`)

```
raw team counts (JSON) ──► FastAPI /predict ──► TeamDiffTransformer ──► StandardScaler ──► LogisticRegression ──► win probabilities
```

| Piece | Tech | Files |
|---|---|---|
| Feature step | Custom scikit-learn transformer: blue/red team counts become the 8 blue-minus-red diff features (missing first blood = 0.5) | `api/transformers.py` |
| Training | scikit-learn `Pipeline` (`StandardScaler` + `LogisticRegression(C=0.1)`), 5-fold stratified CV, trained only on the training split | `api/train_pipeline.py` |
| Model artifact | **joblib** bundle: fitted pipeline plus metadata (features, library versions, train date, CV metrics) | `api/model_bundle.joblib` |
| Web service | **FastAPI** + **Pydantic** input validation (range-checked team counts), served by **uvicorn** | `api/app.py` |
| Hosting | **Modal** ASGI app on a Debian-slim Python 3.13 image, pinned to `api/requirements.txt` | `api/modal_app.py` |

Endpoints: `GET /health`, `GET /pipeline-info`, `POST /predict`, and `/docs` (interactive Swagger docs). The model
loads once at startup. If loading fails, the server stays up and the model endpoints return 503.
`api/local_vs_deployed.md` records that local and deployed predictions match.

## 4. Dashboard

```
prep/prepare.py ──► dashboard/upload_to_supabase.py ──► Supabase (Postgres) ──► dashboard/app.py (Streamlit) ──► Modal
```

| Piece | Tech | Notes |
|---|---|---|
| Data prep | **pandas**, **numpy** | `prep/prepare.py` drops stats a live game can't see (gold, XP, plates, wards), cleans the data and redoes the notebook's split, checked against the same hash |
| Database | **Supabase** (Postgres) | `schema.sql` creates a `matches` table with row-level security, so the public anon key can only read. Uploads use the service-role key, which stays local |
| App | **Streamlit** + **Altair** | Pages through Supabase 1,000 rows at a time and caches the result for an hour |
| Hosting | **Modal** | Debian-slim Python 3.12 image, credentials from a Modal secret, one container (Streamlit sessions live in memory) |

## Dependencies

- `requirements.txt`: requests, urllib3, pillow, pystray (app); numpy, pandas, matplotlib, scipy,
  scikit-learn (notebooks). Building the exe also needs `pyinstaller`.
- `api/requirements.txt`: pinned versions of scikit-learn, numpy, pandas, joblib, fastapi, uvicorn,
  pydantic, modal and requests.
- `dashboard/requirements.txt`: streamlit, supabase, pandas, numpy, altair; `modal` to deploy.

## External services

| Service | Used by | Auth |
|---|---|---|
| Riot Live Client Data API (local) | overlay | none (local HTTPS, self-signed) |
| League client LCU API (local) | lookup window | token from the client's `lockfile` |
| Riot Data Dragon | overlay, build script | none |
| Riot Match API | `build_data.py` | `RIOT_API_KEY` in `.env` |
| Modal | prediction API hosting | `modal deploy` |
| Supabase | dashboard | anon key (read) / service-role key (upload, local only) |
| Modal | dashboard hosting | `modal setup` + Modal secret (`lol-supabase`) |

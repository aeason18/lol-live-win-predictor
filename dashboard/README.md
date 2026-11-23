# Dashboard: Supabase + Streamlit + Modal

Pipeline: `prep/prepare.py` cleans the data and makes the train/test split, `upload_to_supabase.py` loads the
training split into Supabase, `app.py` (Streamlit) reads it back, and `modal_app.py` serves it on Modal.

| File | Role |
|---|---|
| `../prep/prepare.py` | Preparation code: drop non-live columns, clean, add live-style CS, reserve the test set |
| `schema.sql` | The `matches` table, with row-level security so the public key can only read |
| `upload_to_supabase.py` | Uploads the training split (never the held-out test set, player ids or champions) |
| `app.py` | The dashboard: data overview and quality checks, feature explorer |
| `modal_app.py` | Deploys `app.py` on Modal |

## Run it

```
py -m pip install -r dashboard/requirements.txt

# 1. Prepare the data (writes data/training/lol_15min_live_train.csv and the holdout file)
py prep/prepare.py

# 2. In the Supabase SQL editor, run dashboard/schema.sql once. Then:
$env:SUPABASE_URL = "https://<project>.supabase.co"
$env:SUPABASE_SERVICE_KEY = "<service_role key>"       # Project Settings > API; keep it local
py dashboard/upload_to_supabase.py

# 3. Try the dashboard locally
$env:SUPABASE_KEY = "<anon key>"                        # the public key, read-only
streamlit run dashboard/app.py

# 4. Deploy on Modal
modal setup
modal secret create lol-supabase SUPABASE_URL=https://<project>.supabase.co SUPABASE_KEY=<anon key>
modal deploy dashboard/modal_app.py                     # prints the public URL
```

Use `modal serve dashboard/modal_app.py` first for a temporary URL while testing. If your secret isn't called
`lol-supabase`, set `MODAL_SECRET_NAME` before running `modal`.

## Notes

- The dashboard needs the **anon** key. The **service-role** key bypasses row-level security, so it only
  goes in the upload script's environment, never in the Modal secret or the repo.
- Supabase returns 1,000 rows per request, so `app.py` pages through the table; it caches the result for an hour
  (the sidebar has a reload button).
- `prepare.py` reproduces the notebook's split exactly (same 1,632 held-out matches, checked against
  `data/training/lol_15min_test_HOLDOUT.sha256`).

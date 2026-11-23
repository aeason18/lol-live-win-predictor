"""Serve the Streamlit dashboard on Modal.

    pip install modal
    modal setup                                            # once: log in
    modal secret create lol-supabase SUPABASE_URL=https://<project>.supabase.co SUPABASE_KEY=<anon key>
    modal serve  dashboard/modal_app.py                    # temporary dev URL, live-reloads
    modal deploy dashboard/modal_app.py                    # permanent URL

Use the public *anon* key here, never the service-role key: the dashboard only reads, and the table's
row-level security makes it read-only for that key (dashboard/schema.sql).

If your Modal secret has another name, set MODAL_SECRET_NAME to it before running modal.
"""

import os
import subprocess
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
DICTIONARY = HERE.parent / "data" / "training" / "lol_15min_live_data_dictionary.csv"
SECRET_NAME = os.environ.get("MODAL_SECRET_NAME", "lol-supabase")
PORT = 8000

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("streamlit==1.66.1", "supabase==2.33.0", "pandas", "numpy", "altair")
    .add_local_file(HERE / "app.py", "/root/app.py")
    .add_local_file(DICTIONARY, "/root/data_dictionary.csv")
)

app = modal.App("lol-win-dashboard", image=image)


@app.function(
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    max_containers=1,       # Streamlit keeps each visitor's session in memory, so keep them on one container
    scaledown_window=300,   # stay warm for 5 minutes after the last visitor
)
@modal.concurrent(max_inputs=100)
@modal.web_server(PORT, startup_timeout=60)
def dashboard():
    subprocess.Popen([
        "streamlit", "run", "/root/app.py",
        f"--server.port={PORT}", "--server.address=0.0.0.0", "--server.headless=true",
        "--server.enableCORS=false", "--server.enableXsrfProtection=false",
    ])

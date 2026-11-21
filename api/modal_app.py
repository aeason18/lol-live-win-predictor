"""Serve api/app.py's FastAPI app on Modal.

Builds a container image pinned to the exact versions in api/requirements.txt
(the same ones used locally), bakes in transformers.py, app.py and
model_bundle.joblib at the same relative layout they have on disk, then wraps
the existing FastAPI app as an ASGI app - no logic is duplicated here.

Deploy with:
    api\\.venv\\Scripts\\modal deploy api/modal_app.py
"""

from pathlib import Path

import modal

LOCAL_API_DIR = Path(__file__).resolve().parent
REMOTE_API_DIR = "/root/api"

app = modal.App("lol-win-predictor")

image = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install_from_requirements(str(LOCAL_API_DIR / "requirements.txt"))
    .add_local_file(str(LOCAL_API_DIR / "transformers.py"), f"{REMOTE_API_DIR}/transformers.py")
    .add_local_file(str(LOCAL_API_DIR / "app.py"), f"{REMOTE_API_DIR}/app.py")
    .add_local_file(str(LOCAL_API_DIR / "model_bundle.joblib"), f"{REMOTE_API_DIR}/model_bundle.joblib")
)


@app.function(image=image)
@modal.asgi_app()
def fastapi_app():
    import sys

    # Same trick app.py relies on locally: put its own directory on sys.path
    # so `from transformers import ...` resolves the same way in the container.
    sys.path.insert(0, REMOTE_API_DIR)
    from app import app as web_app

    return web_app

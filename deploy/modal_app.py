"""CampusLense on Modal: backend + built frontend in one container, runtime cache on a Volume.

    pip install modal && modal token new                # once, browser login
    cd frontend && npm run build && cd ..               # the app the container serves
    modal deploy deploy/modal_app.py                    # -> https://<workspace>--campuslense-web.modal.run
    modal volume put campuslens-data backend/data/campuslens.sqlite3 /campuslens.sqlite3   # optional: saved profiles

Keys are read from backend/.env at deploy time and stored as a Modal Secret, the Vertex AI service account
(backend/secrets/vertex-sa.json) too; nothing secret goes into the image. FRONTEND_ORIGIN is forced empty so the
container serves the app itself. One container stays warm (MIN_CONTAINERS) so the jury never waits for a cold start.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
APP_DIR = "/root/campuslens"                        # same layout as the repo: data_dir.parents[1] is the repo root
DATA = f"{APP_DIR}/backend/data"
MIN_CONTAINERS = int(os.environ.get("MIN_CONTAINERS", "1"))
SKIP = {"FRONTEND_ORIGIN", "HF_TOKEN", "HIGGSFIELD_API_KEY", "WORLDLABS_API_KEY"}   # not used by the running app


def _secrets() -> dict[str, str]:
    out: dict[str, str] = {}
    env = BACKEND / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#") and m.group(1) not in SKIP and m.group(2):
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    sa = BACKEND / "secrets" / "vertex-sa.json"
    if sa.exists():
        out["VERTEX_SA_JSON"] = sa.read_text(encoding="utf-8")
    return out


app = modal.App("campuslense")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "fonts-dejavu-core", "ffmpeg")
    .pip_install_from_requirements(str(BACKEND / "requirements.txt"), extra_index_url="https://download.pytorch.org/whl/cpu")
    .env({"HF_HOME": "/root/.cache/huggingface", "PYTHONUNBUFFERED": "1"})
    .run_commands("python -c \"import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')\"")
    .add_local_dir(str(BACKEND / "app"), remote_path=f"{APP_DIR}/backend/app")
    .add_local_dir(str(BACKEND / "scripts"), remote_path=f"{APP_DIR}/backend/scripts")
    .add_local_file(str(BACKEND / "data" / "universities.json"), remote_path=f"{APP_DIR}/seed/universities.json")
    .add_local_file(str(BACKEND / "data" / "cost_of_living.json"), remote_path=f"{APP_DIR}/seed/cost_of_living.json")
    .add_local_file(str(BACKEND / "data" / "prewarm_list.json"), remote_path=f"{APP_DIR}/seed/prewarm_list.json")
    .add_local_file(str(BACKEND / "data" / "labels.json"), remote_path=f"{APP_DIR}/seed/labels.json")
    .add_local_file(str(FRONTEND / "public" / "countries.json"), remote_path=f"{APP_DIR}/frontend/public/countries.json")
    .add_local_dir(str(FRONTEND / "dist"), remote_path=f"{APP_DIR}/frontend/dist")
)
volume = modal.Volume.from_name("campuslens-data", create_if_missing=True)
secret = modal.Secret.from_dict(_secrets())


@app.function(image=image, cpu=2.0, memory=6144, timeout=1800, volumes={DATA: volume}, secrets=[secret],
              min_containers=MIN_CONTAINERS, scaledown_window=1200)
@modal.concurrent(max_inputs=48)
@modal.asgi_app()
def web():
    import shutil
    import sys

    os.environ["DATA_DIR"] = DATA
    os.environ["FRONTEND_ORIGIN"] = ""
    sa = os.environ.get("VERTEX_SA_JSON")
    if sa:   # the service account goes where the app looks for it (backend/secrets/vertex-sa.json)
        p = Path(f"{APP_DIR}/backend/secrets/vertex-sa.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sa, encoding="utf-8")
    for f in Path(f"{APP_DIR}/seed").glob("*.json"):   # static datasets live in the image; the Volume keeps the cache
        if not (Path(DATA) / f.name).exists():
            shutil.copy2(f, Path(DATA) / f.name)
    sys.path.insert(0, f"{APP_DIR}/backend")
    from app.main import app as fastapi_app  # noqa: WPS433

    return fastapi_app

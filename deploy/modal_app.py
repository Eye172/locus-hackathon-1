"""CampusLense on Modal (free $30/month, no card): backend + built frontend in one container, cache on a Volume.

    pip install modal && modal setup                    # once, browser login
    modal deploy deploy/modal_app.py                    # -> https://<user>--campuslens-web.modal.run
    modal volume put campuslens-data backend/data/campuslens.sqlite3 /campuslens.sqlite3   # optional: prewarmed cache
    modal volume put campuslens-data backend/data/thumbs /thumbs                           # optional: thumbnails

Keys are read from backend/.env at deploy time and stored as a Modal Secret; FRONTEND_ORIGIN is forced empty so the
container serves the app itself. Set MIN_CONTAINERS=1 below for judging days (no cold start).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
MIN_CONTAINERS = int(os.environ.get("MIN_CONTAINERS", "0"))
KEYS = ("GEMINI_API_KEY", "GEMINI_MODEL", "MAPILLARY_TOKEN", "FLICKR_API_KEY", "GOOGLE_MAPS_API_KEY", "ANTHROPIC_API_KEY")


def _dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#") and m.group(1) in KEYS and m.group(2):
                out[m.group(1)] = m.group(2).strip('"').strip("'")
    out.setdefault("GEMINI_MODEL", "gemini-3.5-flash-lite")
    return out


app = modal.App("campuslens")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "fonts-dejavu-core", "ffmpeg")
    .pip_install_from_requirements(str(BACKEND / "requirements.txt"), extra_index_url="https://download.pytorch.org/whl/cpu")
    .env({"HF_HOME": "/root/.cache/huggingface", "PYTHONUNBUFFERED": "1"})
    .run_commands("python -c \"import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')\"")
    .add_local_dir(str(BACKEND / "app"), remote_path="/root/campuslens/backend/app")
    .add_local_dir(str(BACKEND / "scripts"), remote_path="/root/campuslens/backend/scripts")
    .add_local_file(str(BACKEND / "data" / "universities.json"), remote_path="/root/campuslens/seed/universities.json")
    .add_local_file(str(BACKEND / "data" / "cost_of_living.json"), remote_path="/root/campuslens/seed/cost_of_living.json")
    .add_local_file(str(BACKEND / "data" / "prewarm_list.json"), remote_path="/root/campuslens/seed/prewarm_list.json")
    .add_local_dir(str(FRONTEND / "dist"), remote_path="/root/campuslens/frontend/dist")
)
volume = modal.Volume.from_name("campuslens-data", create_if_missing=True)
secret = modal.Secret.from_dict(_dotenv(BACKEND / ".env"))


@app.function(image=image, cpu=2.0, memory=4096, timeout=600, volumes={"/data": volume}, secrets=[secret],
              min_containers=MIN_CONTAINERS, scaledown_window=900)
@modal.concurrent(max_inputs=32)
@modal.asgi_app()
def web():
    import shutil
    import sys

    os.environ["DATA_DIR"] = "/data"
    os.environ["FRONTEND_ORIGIN"] = ""
    seed = Path("/root/campuslens/seed")
    for f in seed.glob("*.json"):  # static datasets live in the image; the Volume keeps the runtime cache
        if not (Path("/data") / f.name).exists():
            shutil.copy2(f, Path("/data") / f.name)
    sys.path.insert(0, "/root/campuslens/backend")
    from app.main import app as fastapi_app  # noqa: WPS433

    return fastapi_app

"""Deploy CampusLense as ONE Hugging Face Space (Docker, free CPU): backend + built frontend + prewarmed cache.

    HF_TOKEN=hf_...  (write token, put it in backend/.env)  then:
    python deploy/deploy_hf.py --space campuslens            # -> https://huggingface.co/spaces/<you>/campuslens
    python deploy/deploy_hf.py --space campuslens --no-cache # without the sqlite cache and thumbnails (smaller)

The Space README front matter, a single-container Dockerfile and the Space secrets (GEMINI_API_KEY, MAPILLARY_TOKEN)
are written from backend/.env. Nothing secret is committed anywhere.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
STAGE = ROOT / "deploy" / "_stage"

README = """---
title: CampusLense
emoji: 🎓
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# CampusLense — проверенный визуальный профиль университета

LOCUS Startup Hackathon 2026 · кейс 1. Планета → перелёт → 3D-кампус → профиль с фото, источниками и показателем
достоверности. Исходники: https://github.com/pip00sya/locus
"""

DOCKERFILE = """# CampusLense single container: FastAPI backend + built React app + prewarmed cache (CPU only)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_SYMLINKS_WARNING=1 HF_HOME=/app/.cache/huggingface
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY data ./data
COPY frontend/dist /frontend/dist
RUN python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')"
RUN useradd -m -u 1000 user && chown -R user:user /app /frontend
USER user
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
"""


def env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip('"').strip("'")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="campuslens", help="Space name (repo id becomes <user>/<space>)")
    ap.add_argument("--user", default=None, help="HF username/org; default: token owner")
    ap.add_argument("--no-cache", action="store_true", help="do not ship the sqlite cache and thumbnails")
    ap.add_argument("--skip-build", action="store_true", help="reuse frontend/dist as is")
    args = ap.parse_args()

    env = {**env_file(BACKEND / ".env"), **os.environ}
    token = env.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN missing: create a WRITE token at https://huggingface.co/settings/tokens and add HF_TOKEN=... to backend/.env")
    from huggingface_hub import HfApi
    api = HfApi(token=token)
    user = args.user or api.whoami()["name"]
    repo_id = f"{user}/{args.space}"

    if not args.skip_build:
        print("building frontend (same-origin API)…")
        fenv = {**os.environ, "VITE_API_BASE": ""}
        fenv.pop("FRONTEND_ORIGIN", None)
        subprocess.run(["npm", "run", "build"], cwd=FRONTEND, check=True, shell=(os.name == "nt"), env=fenv)

    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ("app", "scripts"):
        shutil.copytree(BACKEND / name, STAGE / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(BACKEND / "requirements.txt", STAGE / "requirements.txt")
    data = STAGE / "data"
    data.mkdir()
    for f in ("universities.json", "cost_of_living.json", "prewarm_list.json", "labels.json"):
        if (BACKEND / "data" / f).exists():
            shutil.copy2(BACKEND / "data" / f, data / f)
    if not args.no_cache:
        for f in BACKEND.glob("data/*.sqlite3"):
            shutil.copy2(f, data / f.name)
        if (BACKEND / "data" / "thumbs").exists():
            shutil.copytree(BACKEND / "data" / "thumbs", data / "thumbs")
    shutil.copytree(FRONTEND / "dist", STAGE / "frontend" / "dist")
    (STAGE / "README.md").write_text(README, encoding="utf-8")
    (STAGE / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (STAGE / ".gitattributes").write_text("*.sqlite3 filter=lfs diff=lfs merge=lfs -text\n*.jpg filter=lfs diff=lfs merge=lfs -text\n*.png filter=lfs diff=lfs merge=lfs -text\n*.geojson filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in STAGE.rglob("*") if p.is_file()) // (1024 * 1024)
    print(f"staged {size} MB at {STAGE}")

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    for key in ("GEMINI_API_KEY", "MAPILLARY_TOKEN", "FLICKR_API_KEY", "GOOGLE_MAPS_API_KEY", "ANTHROPIC_API_KEY"):
        if env.get(key):
            api.add_space_secret(repo_id, key, env[key])
            print("secret set:", key)
    api.add_space_variable(repo_id, "GEMINI_MODEL", env.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    api.add_space_variable(repo_id, "FRONTEND_ORIGIN", "")
    print("uploading… (LFS for images/sqlite)")
    api.upload_folder(folder_path=str(STAGE), repo_id=repo_id, repo_type="space", commit_message="CampusLense deploy",
                      delete_patterns=["*"])
    print(f"done: https://huggingface.co/spaces/{repo_id}  →  app: https://{user}-{args.space}.hf.space")
    print("first build takes ~10 min (torch + CLIP weights); check /api/health there.")


if __name__ == "__main__":
    main()

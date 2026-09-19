# CampusLense backend — CPU only, weights baked in so the first request is fast.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app
# ffmpeg: frames from the middle of short videos (app/pipeline/video_frames.py); without it those sources
# fall back to the designed cover
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 fonts-dejavu-core ffmpeg \n    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY data/universities.json data/cost_of_living.json data/prewarm_list.json ./data/

# download CLIP weights at build time
RUN python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')"

# Hugging Face Spaces expect the app on port 7860 and a non-root user
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]

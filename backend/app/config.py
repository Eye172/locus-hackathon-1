"""Application settings. Values come from environment variables or backend/.env."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "CampusLens"
    contact_email: str = "nnurkhan91@gmail.com"
    data_dir: Path = BACKEND_DIR / "data"
    frontend_origin: str = ""   # e.g. https://campuslens.vercel.app when the SPA is not served by this container

    # Optional API keys. Each one enables an extra photo source; the pipeline works without them.
    mapillary_token: str | None = None
    flickr_api_key: str | None = None
    google_maps_api_key: str | None = None
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5"
    gemini_api_key: str | None = None
    vk_service_token: str | None = None      # free service token of a VK app: wall photos of the official group
    higgsfield_api_key: str | None = None   # cutscene generation only, never called by the profile pipeline
    gemini_model: str = "gemini-3.5-flash-lite"  # 2.5 is closed to new keys; 3.x "flash" thinks for 20-60 s, lite answers in ~2 s
    llm_provider: str = "auto"   # auto | claude | gemini | none

    # Time budget and limits
    pipeline_budget_s: float = 25.0
    source_timeout_s: float = 6.0
    fetch_timeout_s: float = 5.0
    fetch_concurrency: int = 10
    max_candidates: int = 90
    max_per_source: int = 40

    # Vision
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_batch: int = 32
    near_dup_cosine: float = 0.93
    phash_max_distance: int = 8

    # Verification thresholds
    verified_threshold: float = 0.65
    likely_threshold: float = 0.40

    def likely_cut(self) -> float:
        return self.likely_threshold

    # AI inspector: a vision model looks at every candidate (pipeline/ai_inspector.py).
    # 3.5 flash-lite at LOW media resolution = 280 tokens per photo; a batch of 8 answers in ~3 s (≈ $0.002).
    # Small batches in parallel keep the tail latency down: a slow call costs 8 photos, not 16.
    inspect_model: str = "gemini-3.5-flash-lite"
    inspect_batch: int = 8
    inspect_concurrency: int = 6
    inspect_max_photos: int = 112
    inspect_max_street: int = 10      # Mapillary/Flickr street frames: few are informative, they mostly feed the walk tab
    inspect_timeout_s: float = 12.0
    depth_warmup: bool = True
    outdated_years: int = 8
    curate_quota: dict[str, int] = {"campus": 8, "dormitory": 6, "classroom": 6, "library": 6, "lab": 6,
                                    "sports": 6, "student_life": 8, "city": 6}

    # Mirrors (kumi.systems, private.coffee, maps.mail.ru) hang from some networks; the main host answers in ~1 s.
    overpass_urls: list[str] = ["https://overpass-api.de/api/interpreter"]
    overpass_timeout_s: float = 7.0
    campus_budget_s: float = 9.0
    cors_origins: list[str] = ["*"]

    @property
    def user_agent(self) -> str:
        return f"{self.app_name}/0.1 (LOCUS hackathon; contact: {self.contact_email})"

    @property
    def thumbs_dir(self) -> Path:
        d = self.data_dir / "thumbs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def hero_dir(self) -> Path:
        d = self.data_dir / "hero"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sources_status(self) -> dict[str, dict]:
        """Which optional sources are enabled. Shown in the UI so a missing key is never a silent failure."""
        return {
            "official": {"enabled": True, "needs_key": False},
            "commons": {"enabled": True, "needs_key": False},
            "wikipedia": {"enabled": True, "needs_key": False},
            "osm": {"enabled": True, "needs_key": False},
            "mapillary": {"enabled": bool(self.mapillary_token), "needs_key": True, "env": "MAPILLARY_TOKEN"},
            "flickr": {"enabled": bool(self.flickr_api_key), "needs_key": True, "env": "FLICKR_API_KEY"},
            "places": {"enabled": bool(self.google_maps_api_key), "needs_key": True, "env": "GOOGLE_MAPS_API_KEY"},
            "telegram": {"enabled": True, "needs_key": False},
            "youtube": {"enabled": True, "needs_key": False},
            "instagram": {"enabled": True, "needs_key": False},
            "vk": {"enabled": bool(self.vk_service_token), "needs_key": True, "env": "VK_SERVICE_TOKEN"},
            "llm": {"enabled": self.active_llm() != "none", "needs_key": True,
                    "env": "ANTHROPIC_API_KEY or GEMINI_API_KEY", "provider": self.active_llm()},
        }

    def active_llm(self) -> str:
        if self.llm_provider == "none":
            return "none"
        if self.llm_provider in ("claude", "auto") and self.anthropic_api_key:
            return "claude"
        if self.llm_provider in ("gemini", "auto") and self.gemini_api_key:
            return "gemini"
        return "none"


settings = Settings()

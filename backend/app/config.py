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
    likely_threshold: float = 0.30           # with a vision judge available (it can raise or drop borderline photos)
    likely_threshold_no_judge: float = 0.40  # stricter when nobody double-checks

    def likely_cut(self) -> float:
        return self.likely_threshold if self.active_llm() != "none" else self.likely_threshold_no_judge
    judge_max_photos: int = 10
    depth_warmup: bool = True
    judge_low: float = 0.30
    judge_high: float = 0.65
    outdated_years: int = 8

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

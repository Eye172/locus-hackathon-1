"""Application settings. Values come from environment variables or backend/.env."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "CampusLense"
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
    # more Gemini keys from other Google Cloud projects: each project has its own free daily quota (500 requests a
    # model), and a project with billing has none; the inspector moves to the next key when one is spent or blocked
    gemini_api_key_2: str | None = None
    gemini_api_key_3: str | None = None
    # Gemini on Vertex AI, paid from the Google Cloud credits (pipeline/vertex.py): a service-account JSON, by default
    # backend/secrets/vertex-sa.json. When it is there it goes first: no daily cap, many requests in parallel
    vertex_credentials: str | None = None
    vertex_location: str = "global"
    vertex_models: list[str] = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    # the photo inspector's models on Vertex: 3.1-flash-lite there answers text but never an 8-photo batch (0 of 12 in
    # 25 s, 19.09.2026), so the inspector goes from 3.5 on Vertex straight to the AI Studio key
    vertex_inspect_models: list[str] = ["gemini-3.5-flash-lite"]
    vertex_concurrency: int = 8
    # Vertex runs 3.5-flash-lite on Google's shared capacity ("global" is the only location that has it): when that is
    # short a request is queued and answered 429 after up to ~90 s, or never - a third of the inspector's requests in
    # a live build on 19.09. A copy sent again usually gets through at once (healthy: 1.4-4.5 s for 8 photos), so the
    # inspector sends one when the first has not answered in this many seconds, up to vertex_copies in all
    vertex_hedge_s: float = 4.5
    vertex_copies: int = 3
    # where the copies go: 3.5-flash-lite's shared capacity is short for minutes at a time (19 Sep: 12 of 12 requests
    # hung) while 2.5-flash-lite, another pool, answered 12 of 12 in ~3 s, in "global" and in us-central1 alike. It is
    # the weaker judge (hand-labelled photos: P .81 R .80 against 3.5's P .84 R .95), so it only answers when 3.5 does
    # not: (model, location) per copy after the first
    vertex_fallbacks: list[tuple[str, str]] = [("gemini-2.5-flash-lite", "global"), ("gemini-2.5-flash-lite", "us-central1")]
    vk_service_token: str | None = None      # free service token of a VK app: wall photos of the official group
    higgsfield_api_key: str | None = None   # cutscene generation only, never called by the profile pipeline
    serper_api_key: str | None = None       # Google Images search (serper.dev, 2500 free queries)
    scrapecreators_api_key: str | None = None   # Instagram / TikTok posts and TikTok search (scrapecreators.com)
    youtube_api_key: str | None = None      # YouTube Data API v3: videos about the university from any channel
    worldlabs_api_key: str | None = None    # World Labs Marble (3D scenes for the walk; not used by the photo pipeline)
    gemini_model: str = "gemini-3.5-flash-lite"  # 2.5 is closed to new keys; 3.x "flash" thinks for 20-60 s, lite answers in ~2 s
    llm_provider: str = "auto"   # auto | claude | gemini | none

    # Time budget and limits
    pipeline_budget_s: float = 25.0
    source_timeout_s: float = 6.0
    fetch_timeout_s: float = 5.0
    fetch_concurrency: int = 10
    max_candidates: int = 90
    max_per_source: int = 40
    # Background pass. The fast profile answers the case's 30 s; after it nothing is cut off by the clock: the
    # sources still running finish, the social networks are searched in full, the inspector judges the rest, and
    # the open page quietly gets the profile again as photos come in (pipeline/orchestrator.py, Run.background).
    deep_pass: bool = True
    background_guard_s: float = 900.0   # not a budget: only stops something that hangs forever
    background_refresh_s: float = 20.0  # how often the open page gets the grown profile while it runs
    deep_per_source: int = 40
    deep_videos: int = 10          # clips opened per social source (a download plus two ffmpeg seeks each)

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
    # each model has its own daily request quota (500 a day on the free tier); when one is spent the inspector moves
    # to the next instead of rejecting every photo it could not look at. Order = measured quality on the labelled
    # photos (same photos for all three): 3.5 lite precision 0.90 / recall 0.93, 3.1 lite preview 0.86 / 0.78,
    # 3.1 lite 0.82 / 0.69 - the fallbacks err towards rejecting, which is the safe side
    inspect_models: list[str] = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite"]
    inspect_batch: int = 16                 # photos per request: the daily quota counts requests, not photos
    inspect_concurrency: int = 3            # the free Gemini tier answers 429 above ~15 requests a minute
    inspect_max_photos: int = 150
    inspect_max_photos_deep: int = 400   # the background pass keeps judging after the fast profile is on screen
    inspect_max_street: int = 10      # Mapillary/Flickr street frames: few are informative, they mostly feed the walk tab
    inspect_timeout_s: float = 15.0          # 3.1 lite needs ~12 s for 16 photos
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
            "instagram": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True, "env": "SCRAPECREATORS_API_KEY"},
            "instagram_tagged": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True,
                                 "env": "SCRAPECREATORS_API_KEY"},
            "tiktok": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True, "env": "SCRAPECREATORS_API_KEY"},
            "tiktok_search": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True, "env": "SCRAPECREATORS_API_KEY"},
            "tiktok_top": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True,
                           "env": "SCRAPECREATORS_API_KEY"},
            "instagram_search": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True,
                                 "env": "SCRAPECREATORS_API_KEY"},
            "instagram_accounts": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True,
                                   "env": "SCRAPECREATORS_API_KEY"},
            "tiktok_hashtag": {"enabled": bool(self.scrapecreators_api_key), "needs_key": True,
                               "env": "SCRAPECREATORS_API_KEY"},
            "youtube_search": {"enabled": bool(self.youtube_api_key), "needs_key": True, "env": "YOUTUBE_API_KEY"},
            "web_image": {"enabled": bool(self.serper_api_key), "needs_key": True, "env": "SERPER_API_KEY"},
            "map_review": {"enabled": bool(self.serper_api_key), "needs_key": True, "env": "SERPER_API_KEY"},
            "commons_search": {"enabled": True, "needs_key": False},
            "openverse": {"enabled": True, "needs_key": False},
            "vk": {"enabled": bool(self.vk_service_token), "needs_key": True, "env": "VK_SERVICE_TOKEN"},
            "vk_geo": {"enabled": bool(self.vk_service_token), "needs_key": True, "env": "VK_SERVICE_TOKEN"},
            "llm": {"enabled": self.active_llm() != "none", "needs_key": True,
                    "env": "ANTHROPIC_API_KEY or GEMINI_API_KEY", "provider": self.active_llm()},
        }

    def gemini_keys(self) -> list[str]:
        return list(dict.fromkeys(k for k in (self.gemini_api_key, self.gemini_api_key_2, self.gemini_api_key_3) if k))

    def active_llm(self) -> str:
        if self.llm_provider == "none":
            return "none"
        if self.llm_provider in ("claude", "auto") and self.anthropic_api_key:
            return "claude"
        if self.llm_provider in ("gemini", "auto") and self.gemini_api_key:
            return "gemini"
        return "none"


settings = Settings()

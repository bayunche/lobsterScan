from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_project_root() -> Path:
    """从 apps/web-backend/app/config.py 反推到 monorepo 根."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "openclaw" / "openclaw.json").exists():
            return parent
    return here.parent.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    web_backend_host: str = "0.0.0.0"
    web_backend_port: int = 8000

    database_url: str = "sqlite+aiosqlite:///./data/tasks.db"

    openclaw_gateway_host: str = "127.0.0.1"
    openclaw_gateway_port: int = 18789      # 实际默认；可被 .env 覆盖
    huihuibao_channel_listen: str = "127.0.0.1:7860"

    anthropic_api_key: str | None = None
    llm_provider: str = "anthropic"
    video_provider: str = "heygen"
    heygen_api_key: str | None = None

    project_root: Path = _detect_project_root()

    @property
    def outputs_root(self) -> Path:
        return self.project_root / "data" / "outputs"

    @property
    def uploads_root(self) -> Path:
        return self.project_root / "data" / "uploads"


settings = Settings()

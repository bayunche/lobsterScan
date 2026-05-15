from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_project_root() -> Path:
    """从 apps/admin-backend/app/config.py 反推到 monorepo 根."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "openclaw" / "openclaw.json").exists():
            return parent
    return here.parent.parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_backend_host: str = "0.0.0.0"
    admin_backend_port: int = 8100

    project_root: Path = _detect_project_root()

    @property
    def workspaces_root(self) -> Path:
        return self.project_root / "openclaw" / "workspaces"

    @property
    def openclaw_json(self) -> Path:
        return self.project_root / "openclaw" / "openclaw.json"

    @property
    def skills_root(self) -> Path:
        return self.project_root / "skills"

    @property
    def backups_root(self) -> Path:
        return self.project_root / "openclaw" / ".backups"

    @property
    def home_openclaw(self) -> Path:
        return Path.home() / ".openclaw"


settings = Settings()

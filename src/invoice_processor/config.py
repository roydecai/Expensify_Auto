import os
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def get_company_db_path() -> Path:
    configured = os.getenv("COMPANY_DB_PATH")
    if configured:
        configured_path = Path(configured)
        if configured_path.exists():
            return configured_path
    project_root = get_project_root()
    candidates = [
        project_root / "db" / "company.db",
        project_root.parent / "db" / "company.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def get_default_model_id() -> str:
    return os.getenv("ARK_MODEL") or "ep-20260101173816-m4hm8"

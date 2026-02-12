import os
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent


def get_company_db_path() -> Path:
    configured = os.getenv("COMPANY_DB_PATH")
    if configured:
        return Path(configured)
    return get_project_root() / "db" / "company.db"


def get_default_model_id() -> str:
    return os.getenv("ARK_MODEL") or "ep-20260101173816-m4hm8"

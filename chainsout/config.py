"""Carga de configuración: .env, profile.yaml, searches.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    root: Path
    db_path: Path
    telegram_token: str
    telegram_chat_id: str
    llm_backend: str
    llm_model: str
    claude_bin: str
    llm_timeout: int
    classify_batch: int
    umbral_maybe: int


def load_settings(root: Path | None = None) -> Settings:
    root = root or Path(os.environ.get("CHAINSOUT_HOME", ROOT))
    load_dotenv(root / ".env")
    db = Path(os.environ.get("CHAINSOUT_DB", "chainsout.db"))
    return Settings(
        root=root,
        db_path=db if db.is_absolute() else root / db,
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        llm_backend=os.environ.get("LLM_BACKEND", "claude_cli"),
        llm_model=os.environ.get("LLM_MODEL", "claude-opus-5-5"),
        claude_bin=os.environ.get("CLAUDE_BIN", "/usr/local/bin/claude"),
        llm_timeout=int(os.environ.get("LLM_TIMEOUT", "300")),
        classify_batch=max(1, int(os.environ.get("CLASSIFY_BATCH", "5"))),
        umbral_maybe=int(os.environ.get("UMBRAL_MAYBE", "6")),
    )


CONFIG_ENV = "CHAINSOUT_CONFIG"
CONFIG_SECTIONS = ("profile", "searches", "companies")


def _env_config() -> dict | None:
    """CHAINSOUT_CONFIG: un YAML con secciones profile/searches/companies (para CI, vía secret)."""
    raw = os.environ.get(CONFIG_ENV, "").strip()
    if not raw:
        return None
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise SystemExit(f"{CONFIG_ENV} no es un YAML con secciones {CONFIG_SECTIONS}")
    return data


def load_yaml(path: Path) -> dict:
    """Lee profile/searches/companies.yaml; si CHAINSOUT_CONFIG existe, sale de ahí."""
    env = _env_config()
    if env is not None and path.stem in CONFIG_SECTIONS:
        if path.stem not in env:
            raise SystemExit(f"{CONFIG_ENV} no tiene la sección '{path.stem}'")
        return env[path.stem] or {}
    if not path.exists():
        hint = " (cópialo de uno de los ejemplos de examples/)" if path.stem in CONFIG_SECTIONS else ""
        raise SystemExit(f"Falta {path.name}{hint}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

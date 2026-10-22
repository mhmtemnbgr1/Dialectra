"""API anahtarı (.env) ve kullanıcı tercihleri (settings.json).

Her ikisi de uygulama klasöründe tutulur: geliştirirken proje klasörü,
.exe olarak çalışırken .exe'nin bulunduğu klasör.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller ile paketlenmiş .exe
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


ENV_PATH = app_dir() / ".env"
SETTINGS_PATH = app_dir() / "settings.json"
KEY_NAME = "GEMINI_API_KEY"


def load_api_key() -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.environ.get(KEY_NAME, "").strip()


def save_api_key(key: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = [line for line in lines if not line.strip().startswith(f"{KEY_NAME}=")]
    lines.append(f"{KEY_NAME}={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[KEY_NAME] = key


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # tercihleri kaydedememek uygulamayı durdurmamalı

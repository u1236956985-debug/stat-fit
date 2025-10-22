import os
from pathlib import Path
from dotenv import load_dotenv

# Явно грузим .env рядом с config.py
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

def _env(name: str, default: str = "") -> str:
    val = os.getenv(name, default)
    return val.strip() if val is not None else default

BOT_TOKEN = _env("BOT_TOKEN")
WP_API_TOKEN = _env("WP_API_TOKEN")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD", "StartFitAdmin2025")
API_BASE = _env("API_BASE", "https://dev.start-fit.online/app/wp-json/startfitonline/v1").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "settings.json"
DEFAULT_SETTINGS = {
    "username": "",
    "password": "",
    "deepseek_key": "",
    "remember": False,
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(username: str, password: str, deepseek_key: str, remember: bool) -> None:
    data = {
        "username": username if remember else "",
        "password": password if remember else "",
        "deepseek_key": deepseek_key if remember else "",
        "remember": remember,
    }
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

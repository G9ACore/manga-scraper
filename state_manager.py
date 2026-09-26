import json
from pathlib import Path

STATE_FILE = Path("scraper_state.json")

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(url: str, manga_name: str):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({"last_url": url, "last_manga_name": manga_name}, f, ensure_ascii=False, indent=2)

def is_same_manga(current_url: str) -> bool:
    """Проверяет, работаем ли мы с той же мангой, что и в прошлый раз"""
    state = load_state()
    return state.get("last_url") == current_url

def save_metadata(manga_dir: Path, metadata: dict):
    """Сохраняет метаданные в папку манги"""
    metadata_file = manga_dir / "metadata.json"
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

def load_metadata(manga_dir: Path) -> dict:
    """Загружает метаданные из папки манги"""
    metadata_file = manga_dir / "metadata.json"
    if metadata_file.exists():
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

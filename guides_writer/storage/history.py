import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or BASE_DIR / "data" / "history.json"

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            logger.warning("history_load_failed resetting path=%s", self.path)
            return []
        if isinstance(data, dict) and isinstance(data.get("guides"), list):
            return data["guides"]
        return []

    def _save(self, guides: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"guides": guides}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def recent_titles(self, days: int = 40) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        titles = []
        for guide in self._load():
            try:
                recorded = datetime.fromisoformat(guide["date"])
            except (KeyError, ValueError):
                continue
            if recorded >= cutoff:
                titles.append(guide["title"])
        return titles

    def append(
        self,
        title: str,
        source_url: str = "",
        extra: dict | None = None,
        date: str | None = None,
    ) -> None:
        guides = self._load()
        record = {
            "date": date or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "title": title,
            "source_url": source_url,
        }
        if extra:
            record.update(extra)
        guides.append(record)
        self._save(guides)
        logger.info("history_appended title=%s total=%d", title, len(guides))

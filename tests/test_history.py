import json
from datetime import datetime, timedelta, timezone

from guides_writer.storage.history import HistoryStore


def _seed(tmp_path, guides):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"guides": guides}), encoding="utf-8")
    return HistoryStore(path=path)


class TestRecentTitles:
    def test_returns_only_titles_inside_window(self, tmp_path):
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        store = _seed(tmp_path, [
            {"date": recent, "title": "Fresh Guide"},
            {"date": old, "title": "Stale Guide"},
            {"title": "No Date Guide"},
        ])
        titles = store.recent_titles(days=40)
        assert titles == ["Fresh Guide"]

    def test_missing_file_is_empty(self, tmp_path):
        store = HistoryStore(path=tmp_path / "absent.json")
        assert store.recent_titles() == []


class TestRecentSourceUrls:
    def test_returns_urls_inside_window(self, tmp_path):
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        store = _seed(tmp_path, [
            {"date": recent, "title": "A", "source_url": "https://github.com/x/y"},
            {"date": old, "title": "B", "source_url": "https://github.com/old/old"},
            {"date": recent, "title": "C"},  # no url -> skipped
            {"date": recent, "title": "D", "source_url": "  "},  # blank -> skipped
            {"date": "not-a-date", "title": "E", "source_url": "https://e/bad-date"},
        ])
        assert store.recent_source_urls(days=40) == ["https://github.com/x/y"]

    def test_missing_file_is_empty(self, tmp_path):
        store = HistoryStore(path=tmp_path / "absent.json")
        assert store.recent_source_urls() == []

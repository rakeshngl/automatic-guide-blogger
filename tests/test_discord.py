import json

import httpx
import pytest

from guides_writer.deliver.discord import _embed_for, _parse_retry_after, build_deliverer


class FakeResp:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}
        self.text = text or json.dumps(json_data or {})

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


class TestDiscordHelpers:
    def test_embed_fields(self):
        embed = _embed_for(
            {"title": "My Guide", "source": "github_trending", "url": "https://e/1",
             "tagline": "tag", "est_minutes": 40, "tags": ["docker"]},
            "2026-08-22_my-guide.html",
        )
        assert embed["title"] == "My Guide"
        assert embed["color"] == 0x2E7D32
        assert any(f["name"] == "Source" for f in embed["fields"])

    def test_parse_retry_after_json(self):
        resp = FakeResp(429, {"retry_after": 2500})
        assert _parse_retry_after(resp) == pytest.approx(2.5)

    def test_parse_retry_after_header(self):
        resp = FakeResp(429, {}, {"retry-after": "3.2"})
        assert _parse_retry_after(resp) == pytest.approx(3.2)

    def test_parse_retry_after_default(self):
        assert _parse_retry_after(FakeResp(429, {}, {})) == 5.0


class TestDeliverer:
    def test_success(self, tmp_path, monkeypatch):
        file = tmp_path / "2026-08-22_foo.html"
        file.write_text("<html>hi</html>", encoding="utf-8")
        calls = []

        def fake_post(url, data=None, files=None, timeout=None):
            assert "payload_json" in data
            assert "files[0]" in files
            calls.append(url)
            return FakeResp(200, {"id": "123"})

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr("guides_writer.deliver.discord.time.sleep", lambda s: None)

        deliver = build_deliverer("https://discord.com/api/webhooks/x/y")
        ids = deliver([file], [{"title": "Foo", "source": "github_trending",
                                "url": "https://e/1", "status": "ok", "file": str(file)}])
        assert ids == ["123"]
        assert len(calls) == 1

    def test_429_then_success(self, tmp_path, monkeypatch):
        file = tmp_path / "2026-08-22_bar.html"
        file.write_text("<html>hi</html>", encoding="utf-8")
        seq = [FakeResp(429, {"retry_after": 100}, {}), FakeResp(200, {"id": "999"})]
        sleeps: list[float] = []

        def fake_post(url, data=None, files=None, timeout=None):
            return seq.pop(0)

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr("guides_writer.deliver.discord.time.sleep", lambda s: sleeps.append(s))

        deliver = build_deliverer("https://discord.com/api/webhooks/x/y")
        ids = deliver([file], [{"title": "Bar", "status": "ok", "file": str(file)}])
        assert ids == ["999"]
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(0.6, abs=0.01)

    def test_no_webhook_skips(self, tmp_path):
        file = tmp_path / "x.html"
        file.write_text("hi", encoding="utf-8")
        deliver = build_deliverer("")
        assert deliver([file], []) == []

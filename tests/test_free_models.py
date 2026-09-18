import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from guides_writer.llm.free_models import GROQ_PREFERRED, FreeModelCatalog, ModelInfo

FREE_IDS = [
    "sensenova/sensenova-6.8-flash-lite",
    "mistralai/mistral-large-2512",
    "brand/new-free-model",
]
PAID_IDS = ["openai/gpt-5.6-terra", "qwen/qwen3.8-max"]

GROQ_IDS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "whisper-large-v3",
    "brand/groq-new",
]


def _groq_entry(model_id: str) -> dict:
    return {"id": model_id, "object": "model", "owned_by": "groq"}


def _groq_payload() -> dict:
    return {"object": "list", "data": [_groq_entry(m) for m in GROQ_IDS]}


def _model_entry(model_id: str, access_tier: str) -> dict:
    return {
        "id": model_id,
        "object": "model",
        "type": "model",
        "display_name": model_id,
        "access_tier": access_tier,
        "context_length": 128000,
        "pricing": {"input": 0, "output": 0},
    }


def _models_payload() -> dict:
    return {
        "object": "list",
        "data": [_model_entry(m, "free") for m in FREE_IDS]
        + [_model_entry(m, "paid") for m in PAID_IDS],
    }


def _ok_completion() -> dict:
    return {"choices": [{"message": {"content": '{"ok":1}'}}]}


def _client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestDiscovery:
    def test_list_free_only_free_tier(self):
        catalog = FreeModelCatalog(api_key="k", base_url="https://x", verify=False)

        def handler(request):
            assert request.url.path == "/models"
            return httpx.Response(200, json=_models_payload())

        catalog._client = _client_for(handler)
        ids = {m.id for m in catalog.list_free()}
        assert ids == set(FREE_IDS)
        assert not ids & set(PAID_IDS)

    def test_list_free_raises_on_error_status(self):
        catalog = FreeModelCatalog(api_key="k", verify=False)

        def handler(request):
            return httpx.Response(403, json={"error": {"message": "nope"}})

        catalog._client = _client_for(handler)
        with pytest.raises(RuntimeError):
            catalog.list_free()


class TestRanking:
    def test_preferred_first_unknown_last(self):
        catalog = FreeModelCatalog(api_key="k", verify=False)
        free = [
            ModelInfo(id="mistralai/ministral-3b"),
            ModelInfo(id="sensenova/sensenova-6.8-flash-lite"),
            ModelInfo(id="brand/new-free-model"),
            ModelInfo(id="mistralai/mistral-large-2512"),
        ]
        ids = [m.id for m in catalog.rank(free)]
        assert ids == [
            "sensenova/sensenova-6.8-flash-lite",
            "mistralai/mistral-large-2512",
            "mistralai/ministral-3b",
            "brand/new-free-model",
        ]

    def test_custom_preference_override(self):
        catalog = FreeModelCatalog(
            api_key="k", verify=False, preferred=["brand/new-free-model"]
        )
        free = [ModelInfo(id="mistralai/ministral-3b"), ModelInfo(id="brand/new-free-model")]
        ids = [m.id for m in catalog.rank(free)]
        assert ids == ["brand/new-free-model", "mistralai/ministral-3b"]


class TestSelection:
    @pytest.mark.parametrize(
        "fail_ids, expected",
        [
            (set(), "sensenova/sensenova-6.8-flash-lite"),
            ({"sensenova/sensenova-6.8-flash-lite"}, "mistralai/mistral-large-2512"),
        ],
    )
    def test_picks_first_verified_model(self, tmp_path, fail_ids, expected):
        probed = []

        def handler(request):
            if request.url.path == "/models":
                return httpx.Response(200, json=_models_payload())
            body = json.loads(request.content)
            probed.append(body["model"])
            if body["model"] in fail_ids:
                return httpx.Response(403, json={"error": {"message": "needs balance"}})
            return httpx.Response(200, json=_ok_completion())

        catalog = FreeModelCatalog(
            api_key="k",
            base_url="https://x",
            cache_path=tmp_path / "c.json",
            verify=True,
        )
        catalog._client = _client_for(handler)
        res = catalog.select()
        assert res["model"] == expected
        assert res["source"] == "discovered"
        assert res["free_count"] == len(FREE_IDS)
        assert expected in probed
        assert tmp_path.joinpath("c.json").exists()

    def test_fresh_cache_skips_network(self, tmp_path):
        cache = tmp_path / "c.json"
        cache.write_text(
            json.dumps(
                {
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                    "chosen": "sensenova/sensenova-6.8-flash-lite",
                    "free_ids": FREE_IDS,
                }
            ),
            encoding="utf-8",
        )

        def handler(request):
            raise AssertionError("network must not be hit for fresh cache")

        catalog = FreeModelCatalog(api_key="k", cache_path=cache, verify=True)
        catalog._client = _client_for(handler)
        res = catalog.select()
        assert res["model"] == "sensenova/sensenova-6.8-flash-lite"
        assert res["source"] == "cache"

    def test_expired_cache_rediscovered(self, tmp_path):
        cache = tmp_path / "c.json"
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        cache.write_text(
            json.dumps({"discovered_at": old, "chosen": "stale/stale-model", "free_ids": []}),
            encoding="utf-8",
        )

        def handler(request):
            if request.url.path == "/models":
                return httpx.Response(200, json=_models_payload())
            return httpx.Response(200, json=_ok_completion())

        catalog = FreeModelCatalog(api_key="k", base_url="https://x", cache_path=cache, verify=True)
        catalog._client = _client_for(handler)
        res = catalog.select()
        assert res["source"] == "discovered"
        assert res["model"] == "sensenova/sensenova-6.8-flash-lite"

    def test_no_free_models_returns_none(self, tmp_path):
        payload = {"object": "list", "data": [_model_entry(m, "paid") for m in PAID_IDS]}

        def handler(request):
            return httpx.Response(200, json=payload)
        catalog = FreeModelCatalog(
            api_key="k", cache_path=tmp_path / "c.json", verify=True
        )
        catalog._client = _client_for(handler)
        res = catalog.select()
        assert res["model"] is None
        assert res["free_count"] == 0

    def test_all_probes_fail_returns_none(self, tmp_path):
        def handler(request):
            if request.url.path == "/models":
                return httpx.Response(200, json=_models_payload())
            return httpx.Response(403, json={"error": {"message": "needs balance"}})

        catalog = FreeModelCatalog(
            api_key="k", base_url="https://x", cache_path=tmp_path / "c.json", verify=True
        )
        catalog._client = _client_for(handler)
        res = catalog.select()
        assert res["model"] is None
        assert set(res["checked"]) == set(FREE_IDS)
        assert res["checked"][0] == "sensenova/sensenova-6.8-flash-lite"

    def test_discovery_error_returns_none_with_error(self, tmp_path):
        catalog = FreeModelCatalog(api_key="k", cache_path=tmp_path / "c.json")
        catalog._client = _client_for(lambda r: httpx.Response(500, text="boom"))
        res = catalog.select()
        assert res["model"] is None
        assert "error" in res


class TestGroqProfile:
    def test_tier_none_keeps_every_listed_model(self):
        catalog = FreeModelCatalog(api_key="k", base_url="https://x", verify=False, tier=None)

        def handler(request):
            return httpx.Response(200, json=_groq_payload())

        catalog._client = _client_for(handler)
        ids = {m.id for m in catalog.list_free()}
        assert ids == set(GROQ_IDS)

    def test_probe_sends_json_object_mode(self):
        catalog = FreeModelCatalog(api_key="k", base_url="https://x", verify=False, tier=None, json_mode=True)
        sent = {}

        def handler(request):
            sent["body"] = json.loads(request.content)
            return httpx.Response(200, json=_ok_completion())

        catalog._client = _client_for(handler)
        assert catalog.probe("openai/gpt-oss-20b")
        assert sent["body"]["response_format"] == {"type": "json_object"}

    def test_keep_configured_model_while_listed(self, tmp_path):
        """Pinned model still on the list is used with zero probes."""
        catalog = FreeModelCatalog(
            api_key="k",
            base_url="https://x",
            cache_path=tmp_path / "c.json",
            verify=True,
            tier=None,
            json_mode=True,
        )

        def handler(request):
            if request.url.path == "/chat/completions":
                raise AssertionError("must not probe a kept model")
            return httpx.Response(200, json=_groq_payload())

        catalog._client = _client_for(handler)
        res = catalog.select(keep="qwen/qwen3.8-27b")
        assert res["model"] == "qwen/qwen3.8-27b"
        assert res["source"] == "listed"
        assert tmp_path.joinpath("c.json").exists()

    def test_keep_drops_to_next_model_after_rename(self, tmp_path):
        """When the pinned model vanishes, the best replacement is probed+picked."""
        picked = []

        def payload():
            return {"object": "list", "data": [_groq_entry(m) for m in GROQ_IDS if m != "qwen/qwen3.8-27b"]}

        def handler(request):
            if request.url.path == "/models":
                return httpx.Response(200, json=payload())
            body = json.loads(request.content)
            picked.append(body["model"])
            if body["model"] == "whisper-large-v3":
                return httpx.Response(400, json={"error": {"message": "not a chat model"}})
            return httpx.Response(200, json=_ok_completion())

        catalog = FreeModelCatalog(
            api_key="k",
            base_url="https://x",
            cache_path=tmp_path / "c.json",
            verify=True,
            tier=None,
            json_mode=True,
            preferred=GROQ_PREFERRED,
        )
        catalog._client = _client_for(handler)
        res = catalog.select(keep="qwen/qwen3.8-27b")
        assert res["model"] == GROQ_PREFERRED[0]
        assert res["source"] == "discovered"
        assert "qwen/qwen3.8-27b" not in picked

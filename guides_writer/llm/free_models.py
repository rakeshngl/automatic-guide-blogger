import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.xkiro.com/v1"
MODELS_ENDPOINT = "/models"

# Known free-tier models, best-first for JSON idea extraction. Inserting new
# ids here gives them priority; unknown free ids still get tried (ranked last)
# so newly added xkiro free models are picked up without a code change.
# NOTE: sensenova flash-lite leads because it returns reliable json_object
# output fast; mistral-large responds to probes but stalls on json_object.
DEFAULT_PREFERRED = [
    "sensenova/sensenova-6.8-flash-lite",
    "sensenova/sensenova-6.7-flash-lite",
    "mistralai/mistral-large-2512",
    "mistralai/mistral-medium-3.5",
    "mistralai/mistral-small-2603",
    "mistralai/ministral-8b",
    "mistralai/ministral-14b",
    "mistralai/ministral-3b",
    "mistralai/codestral-2508",
    "mistralai/devstral-medium",
]


@dataclass
class ModelInfo:
    id: str
    display_name: str = ""
    context_length: int = 0
    pricing: dict | None = None


class FreeModelCatalog:
    """Discovers the provider's currently-free models and picks the best one.

    xkiro periodically adds/removes free tier models (the old ``:free``
    suffixes are already gone). This catalog:
      1. calls ``GET /v1/models`` and keeps only ``access_tier == "free"``
      2. ranks them by preference (known-good first, unknowns last)
      3. optionally probes each until one returns a valid completion
      4. caches the pick locally so regular runs skip the probe entirely
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        cache_path: str | Path | None = None,
        ttl_hours: int = 24,
        preferred: list[str] | None = None,
        verify: bool = True,
        max_probe: int = 5,
        timeout: float = 25.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.cache_path = Path(cache_path) if cache_path else None
        self.ttl_seconds = ttl_hours * 3600
        self.preferred = preferred or list(DEFAULT_PREFERRED)
        self.verify = verify
        self.max_probe = max_probe
        self.timeout = timeout
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def list_free(self) -> list[ModelInfo]:
        resp = self._client.get(f"{self.base_url}{MODELS_ENDPOINT}")
        if resp.status_code != 200:
            raise RuntimeError(
                f"GET {MODELS_ENDPOINT} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        items = resp.json().get("data", [])
        return [
            ModelInfo(
                id=str(m["id"]),
                display_name=str(m.get("display_name", "")),
                context_length=int(m.get("context_length") or 0),
                pricing=m.get("pricing"),
            )
            for m in items
            if m.get("access_tier") == "free" and isinstance(m.get("id"), str)
        ]

    def rank(self, free: list[ModelInfo]) -> list[ModelInfo]:
        by_id = {m.id: m for m in free}
        ordered = [by_id.pop(mid) for mid in self.preferred if mid in by_id]
        ordered += [by_id[mid] for mid in sorted(by_id)]
        return ordered

    def probe(self, model: str) -> bool:
        probe = {
            "model": model,
            "messages": [
                {"role": "user", "content": 'Reply with exactly the JSON object {"ok":1}'}
            ],
            "max_tokens": 20,
            "temperature": 0,
        }
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                json=probe,
                timeout=min(self.timeout, 12.0),
            )
            if resp.status_code != 200:
                return False
            content = resp.json()["choices"][0]["message"].get("content", "")
            return '"ok"' in content and "{" in content
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return False

    def _load_cache(self) -> dict | None:
        if not self.cache_path or not self.cache_path.exists():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            discovered = datetime.fromisoformat(payload["discovered_at"]).timestamp()
            if time.time() - discovered > self.ttl_seconds:
                return None
            return payload
        except (ValueError, KeyError, TypeError, OSError):
            return None

    def _write_cache(self, payload: dict) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def select(self) -> dict:
        cached = self._load_cache()
        if cached and cached.get("chosen"):
            logger.info(
                "free_model_from_cache model=%s source=cache",
                cached["chosen"],
            )
            return {
                "model": cached["chosen"],
                "source": "cache",
                "free_count": len(cached.get("free_ids", [])),
            }
        try:
            free = self.list_free()
        except (RuntimeError, httpx.HTTPError) as exc:
            logger.warning("free_model_discovery_failed err=%s", str(exc)[:150])
            return {"model": None, "error": str(exc)[:150]}
        ordered = self.rank(free)
        chosen = None
        checked: list[str] = []
        if self.verify and ordered:
            candidates = ordered[: self.max_probe]
            with ThreadPoolExecutor(max_workers=5) as pool:
                ok_flags = list(pool.map(self.probe, [m.id for m in candidates]))
            checked = [m.id for m in candidates]
            chosen = next(
                (m.id for m, ok in zip(candidates, ok_flags) if ok), None
            )
        elif ordered:
            chosen = ordered[0].id
        self._write_cache(
            {
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "chosen": chosen,
                "free_ids": [m.id for m in ordered],
                "checked": checked,
                "verified": self.verify,
            }
        )
        if not chosen:
            logger.warning("free_model_none_usable checked=%s", ",".join(checked[:5]))
            return {
                "model": None,
                "free_count": len(free),
                "checked": checked,
            }
        logger.info(
            "free_model_picked model=%s source=discovered free=%d checked=%d",
            chosen,
            len(free),
            len(checked),
        )
        return {
            "model": chosen,
            "source": "discovered",
            "free_count": len(free),
            "checked": checked,
        }
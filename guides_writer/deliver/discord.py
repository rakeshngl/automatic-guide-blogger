import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

COLOR_UVF = 0x2E7D32


def _embed_for(result: dict, file_name: str) -> dict:
    title = result.get("title", file_name)
    url = result.get("url", "")
    source = result.get("source", "")
    tagline = result.get("tagline", "")
    est = result.get("est_minutes")
    tags = result.get("tags", [])
    fields = []
    if source or url:
        link = f"[{source}]({url})" if source and url else (url or source)
        fields.append({"name": "Source", "value": link, "inline": True})
    if est:
        fields.append({"name": "Est.", "value": f"{est} min", "inline": True})
    if tags:
        fields.append({"name": "Tags", "value": ", ".join(f"`{t}`" for t in tags[:4]), "inline": False})
    fields.append({"name": "File", "value": f"`{file_name}`", "inline": False})
    embed: dict = {
        "title": title[:256],
        "color": COLOR_UVF,
        "fields": fields,
        "footer": {"text": "UVF Guides Drafts"},
        "timestamp": result.get("timestamp", ""),
    }
    if tagline:
        embed["description"] = tagline[:4000]
    return embed


def _parse_retry_after(resp: httpx.Response) -> float:
    try:
        data = resp.json()
        if isinstance(data, dict) and "retry_after" in data:
            return float(data["retry_after"]) / 1000.0
    except Exception:
        pass
    header = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return 5.0


def send_one(webhook_url: str, path: Path, result: dict) -> str | None:
    embed = _embed_for(result, path.name)
    payload = {"embeds": [embed], "content": f"New draft: **{result.get('title', path.name)}**"}
    url = webhook_url + ("?wait=true" if "?wait" not in webhook_url else "")

    for attempt in range(1, 5):
        try:
            with path.open("rb") as f:
                files = {"files[0]": (path.name, f.read(), "text/html")}
                resp = httpx.post(
                    url,
                    data={"payload_json": json.dumps(payload)},
                    files=files,
                    timeout=40,
                )
        except httpx.HTTPError as exc:
            logger.warning("discord_post_error attempt=%d err=%s", attempt, exc)
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 30))
            continue

        if resp.status_code == 429:
            wait = _parse_retry_after(resp)
            logger.warning("discord_rate_limited wait=%.1fs attempt=%d", wait, attempt)
            if attempt == 4:
                resp.raise_for_status()
            time.sleep(wait + 0.5)
            continue

        if resp.status_code in (500, 502, 503, 504):
            logger.warning("discord_5xx status=%d attempt=%d", resp.status_code, attempt)
            if attempt == 4:
                resp.raise_for_status()
            time.sleep(min(2 ** attempt, 30))
            continue

        resp.raise_for_status()
        try:
            data = resp.json()
            return str(data.get("id", "")) if isinstance(data, dict) else None
        except Exception:
            return None

    return None


def build_deliverer(webhook_url: str):
    def deliver(files: list[Path], guide_results: list[dict]) -> list[str | None]:
        if not webhook_url:
            logger.warning("discord_deliver_skipped no_webhook_url")
            return []
        results_by_file = {r.get("file", ""): r for r in guide_results if r.get("status") == "ok"}
        ids: list[str | None] = []
        for path in files:
            result = results_by_file.get(str(path), {"title": path.stem, "url": ""})
            message_id = send_one(webhook_url, path, result)
            ids.append(message_id)
            logger.info("discord_delivered file=%s message_id=%s", path.name, message_id)
        return ids

    return deliver

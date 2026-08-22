import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from guides_writer.agents.selector import TopicSelector
from guides_writer.agents.writer import GuideWriter
from guides_writer.llm.client import LLMClient
from guides_writer.render.renderer import render_guide
from guides_writer.storage.history import HistoryStore

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "guide"


def fetch_pool(adapters) -> tuple[list, list[str]]:
    pool = []
    failures = []
    for adapter in adapters:
        try:
            pool.extend(adapter.fetch())
        except Exception as exc:
            failures.append(adapter.name)
            logger.warning("source_failed source=%s err=%s", adapter.name, exc)
    return pool, failures


def run_pipeline(settings, adapters=None, llm_client: LLMClient | None = None,
                 out_dir: Path | None = None, deliver=None, runs_dir: Path | None = None,
                 history_path: Path | None = None) -> dict:
    out_dir = out_dir or BASE_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    adapters = adapters if adapters is not None else None
    if adapters is None:
        from guides_writer.__main__ import build_adapters

        adapters = build_adapters(settings)

    candidates, source_failures = fetch_pool(adapters)
    logger.info("pool_fetched candidates=%d failed_sources=%s", len(candidates), source_failures)
    if not candidates:
        summary = {"date": date_str, "status": "no_candidates",
                   "failed_sources": source_failures}
        _write_run_summary(summary, runs_dir)
        return summary

    llm_client = llm_client or LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
    )
    history = HistoryStore(path=history_path)
    selection = TopicSelector(llm_client).select(
        candidates=candidates,
        count=settings.guides_per_run,
        exclude_titles=history.recent_titles(),
    )
    logger.info(
        "selection_done picks=%d degraded=%s dropped=%s",
        len(selection.picks), selection.degraded, selection.duplicates_dropped,
    )
    if not selection.picks:
        summary = {"date": date_str, "status": "selection_failed",
                   "failed_sources": source_failures}
        _write_run_summary(summary, runs_dir)
        return summary

    writer = GuideWriter(llm_client)
    results = []
    delivered_files: list[Path] = []
    candidate_by_url = {c.url.rstrip("/"): c for c in candidates}
    used_filenames: set[str] = set()

    for idx, pick in enumerate(selection.picks):
        if idx > 0 and isinstance(llm_client, LLMClient):
            time.sleep(38)
        entry = {"title": pick.title, "source": pick.source, "url": pick.source_url}
        try:
            context = _safe_enrich(candidate_by_url.get(pick.source_url.rstrip("/")) or pick)
            guide = writer.write_guide(pick, context)
            html_out = render_guide(guide)
            filename = f"{date_str}_{slugify(guide.meta.title)}.html"
            counter = 2
            while filename in used_filenames:
                filename = f"{date_str}_{slugify(guide.meta.title)}-{counter}.html"
                counter += 1
            used_filenames.add(filename)
            path = out_dir / filename
            path.write_text(html_out, encoding="utf-8")
            entry.update({"status": "ok", "file": str(path), "tagline": guide.meta.tagline})
            delivered_files.append(path)
            if not settings.dry_run:
                history.append(title=guide.meta.title, source_url=pick.source_url,
                               extra={"slug": filename})
        except Exception as exc:
            logger.exception("guide_pipeline_failed title=%s", pick.title)
            entry["status"] = f"failed: {exc}"
        results.append(entry)

    delivery_errors: list[str] = []
    delivery_ids: list[str | None] = []
    if deliver is not None and not settings.dry_run and delivered_files:
        try:
            maybe_ids = deliver(delivered_files, results)
            if isinstance(maybe_ids, list):
                delivery_ids = maybe_ids
        except Exception as exc:
            logger.exception("delivery_failed")
            delivery_errors.append(str(exc))

    ok_count = sum(1 for r in results if r["status"] == "ok")
    summary = {
        "date": date_str,
        "status": "ok" if ok_count == len(results) and ok_count else (
            "partial" if ok_count else "all_guides_failed"
        ),
        "degraded_selection": selection.degraded,
        "duplicates_dropped": selection.duplicates_dropped,
        "failed_sources": source_failures,
        "dry_run": settings.dry_run,
        "guides": results,
        "delivery_ids": delivery_ids,
        "delivery_errors": delivery_errors,
    }
    _write_run_summary(summary, runs_dir)
    logger.info("run_complete status=%s ok=%d/%d", summary["status"], ok_count, len(results))
    return summary


def _safe_enrich(topic) -> str:
    from guides_writer.agents.writer import enrich

    try:
        return enrich(topic)
    except Exception as exc:
        logger.warning("enrich_failed title=%s err=%s", getattr(topic, "title", "?"), exc)
        title = getattr(topic, "title", "")
        tagline = getattr(topic, "tagline", "")
        return f"Title: {title}" + (f"\nTagline: {tagline}" if tagline else "")


def _write_run_summary(summary: dict, runs_dir: Path | None = None) -> None:
    runs_dir = runs_dir or (BASE_DIR / "data" / "runs")
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{summary['date']}.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

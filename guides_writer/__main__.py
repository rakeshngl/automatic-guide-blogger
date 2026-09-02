import argparse
import sys

from guides_writer.config import get_settings
from guides_writer.llm.client import LLMClient
from guides_writer.sources.base import dedupe
from guides_writer.sources.devto import DevToAdapter
from guides_writer.sources.github_trending import GitHubTrendingAdapter
from guides_writer.sources.hf_spaces import HFSpaceAdapter
from guides_writer.sources.hn_show import HNShowAdapter
from guides_writer.sources.producthunt import ProductHuntAdapter
from guides_writer.sources.reddit import RedditSelfHostedAdapter
from guides_writer.sources.gmail_digest import GmailDigestAdapter
from guides_writer.sources.taaft import TaaftAdapter
from guides_writer.utils.logging import setup_logging


def build_adapters(settings) -> list:
    adapters = []
    if settings.ph_api_token:
        adapters.append(ProductHuntAdapter(developer_token=settings.ph_api_token))
    else:
        print("  (skipping producthunt: PH_API_TOKEN not set)")
    adapters.append(GitHubTrendingAdapter())
    adapters.append(TaaftAdapter())
    adapters.append(HNShowAdapter())
    adapters.append(HFSpaceAdapter())
    adapters.append(DevToAdapter())
    adapters.append(RedditSelfHostedAdapter())
    if settings.gmail_user and settings.gmail_app_password:
        email_llm = None
        if settings.email_llm_api_key:
            email_llm = LLMClient(
                api_key=settings.email_llm_api_key,
                base_url=settings.email_llm_base_url or "https://api.xkiro.com/v1",
                model=settings.email_llm_model or "qwen/qwen3.8-max:free",
            )
        adapters.append(
            GmailDigestAdapter(
                user=settings.gmail_user,
                app_password=settings.gmail_app_password,
                sender=settings.gmail_sender,
                allowed_subreddits={
                    s.strip() for s in settings.gmail_allowed_subreddits.split(",") if s.strip()
                },
                llm_client=email_llm,
                lookback_days=settings.gmail_lookback_days,
            )
        )
    else:
        print("  (skipping gmail_digest: GMAIL_USER / GMAIL_APP_PASSWORD not set)")
    return adapters


def cmd_hello() -> int:
    settings = get_settings()
    client = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        fallback_model=getattr(settings, "llm_fallback_model", None),
        fallback_base_url=getattr(settings, "llm_fallback_base_url", None),
    )
    reply = client.chat(
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly one word: pong",
            }
        ],
        temperature=0.0,
    )
    print(f"LLM ({settings.llm_model}) says: {reply.strip()}")
    return 0


def cmd_test_sources() -> int:
    settings = get_settings()
    pool = []
    failures = []
    for adapter in build_adapters(settings):
        try:
            items = adapter.fetch()
            pool.extend(items)
            print(f"[ok] {adapter.name}: {len(items)} candidates")
            for item in items[:5]:
                votes = item.metrics.get("votes") or item.metrics.get("stars_today")
                extra = f" ({votes})" if votes is not None else ""
                tagline = (item.tagline or "")[:60]
                print(f"     {item.rank:>2}. {item.title}{extra} - {tagline}")
        except Exception as exc:
            failures.append(adapter.name)
            print(f"[FAIL] {adapter.name}: {exc}")
    pool = dedupe(pool)
    print(f"\nmerged pool: {len(pool)} unique candidates, failed sources: {failures or 'none'}")
    return 1 if not pool else 0


def cmd_select(count: int | None = None, record: bool = False) -> int:
    import json

    from guides_writer.agents.selector import TopicSelector
    from guides_writer.storage.history import HistoryStore

    settings = get_settings()
    candidates = []
    for adapter in build_adapters(settings):
        try:
            candidates.extend(adapter.fetch())
        except Exception as exc:
            print(f"[FAIL] {adapter.name}: {exc}")
    if not candidates:
        print("no candidates from any source")
        return 2

    client = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        fallback_model=getattr(settings, "llm_fallback_model", None),
        fallback_base_url=getattr(settings, "llm_fallback_base_url", None),
    )
    history = HistoryStore()
    selector = TopicSelector(client)
    result = selector.select(
        candidates=candidates,
        count=count or settings.guides_per_run,
        exclude_titles=history.recent_titles(days=settings.history_days),
    )
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    if record:
        for pick in result.picks:
            history.append(title=pick.title, source_url=pick.source_url, extra={"tags": pick.tags})
    return 0 if result.picks else 3


def cmd_render_sample() -> int:
    from pathlib import Path

    from guides_writer.render.renderer import render_guide
    from guides_writer.render.sample_data import SAMPLE_GUIDE

    html_out = render_guide(SAMPLE_GUIDE)
    out_path = Path("out") / "sample.html"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    print(f"rendered {len(html_out)} bytes -> {out_path}")
    return 0


def cmd_run(dry_run: bool) -> int:
    from guides_writer.deliver.discord import build_deliverer
    from guides_writer.pipeline import run_pipeline

    settings = get_settings()
    if dry_run:
        settings.dry_run = True
    deliver = None
    if not settings.dry_run and settings.discord_webhook_url:
        deliver = build_deliverer(settings.discord_webhook_url)
    elif not settings.dry_run:
        print("  (no DISCORD_WEBHOOK_URL set — guides will be rendered locally only)")
    summary = run_pipeline(settings, deliver=deliver)
    print(f"run status : {summary['status']}")
    print(f"dry run    : {summary.get('dry_run')}")
    print(f"degraded   : {summary.get('degraded_selection')}")
    for guide in summary.get("guides", []):
        marker = "OK  " if guide["status"] == "ok" else "FAIL"
        print(f" [{marker}] {guide['title']} -> {guide.get('file', guide['status'])}")
    status = summary["status"]
    if summary.get("delivery_errors"):
        return 4
    if status == "no_candidates":
        return 2
    if status == "selection_failed":
        return 3
    if not summary.get("guides"):
        return 3
    ok_count = sum(1 for g in summary.get("guides", []) if g["status"] == "ok")
    if ok_count == 0:
        return 3  # all guides failed -> nothing to publish
    # exit 0 whenever >=1 guide was produced, even if partial (other guides failed)
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="guides_writer", description="Automatic Guides Writer agent"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hello", help="verify LLM API connectivity")
    sub.add_parser("test-sources", help="fetch all sources live and summarize")
    select_parser = sub.add_parser("select", help="pick today's guide-worthy topics")
    select_parser.add_argument("--count", type=int, default=None)
    select_parser.add_argument("--record", action="store_true", help="persist picks to history")
    sub.add_parser("render-sample", help="render the bundled sample guide to out/sample.html")
    run_parser = sub.add_parser("run", help="full pipeline: discover -> select -> write -> render")
    run_parser.add_argument("--dry-run", action="store_true", help="skip delivery and history updates")

    args = parser.parse_args(argv)
    setup_logging()

    if args.command == "hello":
        return cmd_hello()
    if args.command == "test-sources":
        return cmd_test_sources()
    if args.command == "select":
        return cmd_select(count=args.count, record=args.record)
    if args.command == "render-sample":
        return cmd_render_sample()
    if args.command == "run":
        return cmd_run(dry_run=args.dry_run)
    return 1


if __name__ == "__main__":
    sys.exit(main())

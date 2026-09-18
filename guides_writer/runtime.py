"""Runtime wiring: adapter construction and LLM model resolution.

Kept out of ``__main__`` so the pipeline can build adapters and resolve
models without importing the CLI module (which would be circular)."""
import logging

from guides_writer.config import BASE_DIR
from guides_writer.llm.client import LLMClient
from guides_writer.llm.free_models import GROQ_PREFERRED, FreeModelCatalog
from guides_writer.sources.devto import DevToAdapter
from guides_writer.sources.github_trending import GitHubTrendingAdapter
from guides_writer.sources.gmail_digest import GmailDigestAdapter
from guides_writer.sources.hn_show import HNShowAdapter
from guides_writer.sources.producthunt import ProductHuntAdapter
from guides_writer.sources.reddit import RedditSelfHostedAdapter

logger = logging.getLogger(__name__)

DEFAULT_EMAIL_MODEL = "sensenova/sensenova-6.8-flash-lite"


def resolve_email_model(settings):
    """Pick an xkiro free model automatically, falling back to configured.

    Returns ``(model, detail)`` where ``detail`` explains the decision so the
    run log shows whether the model came from the cache, live discovery, or
    the configured default.
    """
    if settings.email_llm_auto_select and settings.email_llm_api_key:
        preferred = [
            m.strip()
            for m in settings.email_llm_prefer_order.split(",")
            if m.strip()
        ] or None
        catalog = FreeModelCatalog(
            api_key=settings.email_llm_api_key,
            base_url=settings.email_llm_base_url or "https://api.xkiro.com/v1",
            cache_path=BASE_DIR / "data" / "cache" / "email_free_models.json",
            preferred=preferred,
        )
        result = catalog.select()
        if result.get("model"):
            return result["model"], result
        logger.warning(
            "email_model_auto_select_failed err=%s",
            result.get("error", "all probes failed"),
        )
    return settings.email_llm_model or DEFAULT_EMAIL_MODEL, {"source": "config"}


def resolve_llm_model(settings):
    """Resolve the selector model, with auto-fallback when it disappears.

    Honors the configured ``LLM_MODEL`` while it stays on the provider's
    model list (no probing - it is already a known-good pin). If the provider
    renames/removes it (like Groq's qwen3.6 -> qwen3.8), picks the best
    still-available model from the live list so the run keeps going.
    """
    if getattr(settings, "llm_auto_select", False) and getattr(settings, "llm_api_key", None):
        preferred = [
            m.strip()
            for m in (getattr(settings, "llm_prefer_order", "") or "").split(",")
            if m.strip()
        ] or list(GROQ_PREFERRED)
        catalog = FreeModelCatalog(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            cache_path=BASE_DIR / "data" / "cache" / "llm_models.json",
            preferred=preferred,
            tier=None,
            json_mode=True,
        )
        result = catalog.select(keep=settings.llm_model)
        if result.get("model"):
            return result["model"], result
        logger.warning(
            "llm_model_auto_select_failed err=%s",
            result.get("error", "all probes failed"),
        )
    return settings.llm_model, {"source": "config"}


def build_adapters(settings) -> list:
    adapters = []
    if settings.ph_api_token:
        adapters.append(ProductHuntAdapter(developer_token=settings.ph_api_token, limit=8))
    else:
        logger.warning("producthunt_skipped no_ph_api_token")
    adapters.append(GitHubTrendingAdapter())
    adapters.append(HNShowAdapter())
    adapters.append(DevToAdapter(limit=10))
    adapters.append(RedditSelfHostedAdapter())
    if settings.gmail_user and settings.gmail_app_password:
        email_llm = None
        if settings.email_llm_api_key:
            model, _detail = resolve_email_model(settings)
            email_llm = LLMClient(
                api_key=settings.email_llm_api_key,
                base_url=settings.email_llm_base_url or "https://api.xkiro.com/v1",
                model=model,
            )
            logger.info("email_digest_llm_model model=%s", model)
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
        logger.warning("gmail_digest_skipped missing_gmail_settings")
    return adapters

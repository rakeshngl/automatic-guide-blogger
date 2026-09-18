"""Scan rendered guides for abusive injected links before they ship.

Prompt injection can push spam/malware URLs into generated guide prose. The
final HTML body is autoescaped, so an injected ``<a href>`` cannot survive in
prose - but plain-text URLs would still appear (and get crawled by search
engines), and anything the template itself emits still needs a sanity check
before the file is written and published.
"""
import logging
import re
from urllib.parse import urlparse

from guides_writer.render.schema import Guide

logger = logging.getLogger(__name__)

UNSAFE_SCHEME_RE = re.compile(r"(?i)\b(?:javascript|vbscript|data|file|blob):")
ATTR_URL_RE = re.compile(r"""(?i)\b(?:href|src)\s*=\s*["']([^"']+)["']""")
URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")

_LOCAL_HINTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "example.com",
    "example.org",
    "test",
}

TEMPLATE_HOSTS = {
    "uvfarms.in",
    "pagead2.googlesyndication.com",
    "googlesyndication.com",
}


class ContentGuardError(Exception):
    """Raised when a rendered guide violates the link-safety policy."""


def _prose_fields(guide: Guide) -> list[str]:
    texts = [guide.meta.title, guide.meta.tagline, guide.meta.hero_paragraph]
    texts.extend(list(guide.intro))
    if guide.warning_box:
        texts.append(guide.warning_box.heading)
        texts.extend(list(guide.warning_box.bullets))
    for phase in guide.phases:
        texts.append(phase.title)
        texts.extend(list(phase.prose))
    for row in guide.checklist:
        texts.append(row.check)
        texts.append(row.command_why)
    if guide.closing_alert:
        texts.append(guide.closing_alert.heading)
        texts.append(guide.closing_alert.body)
    return [t for t in texts if t]


def _external_hosts(texts: list[str], allowed: set[str]) -> set[str]:
    hosts = set()
    for text in texts:
        for url in URL_RE.findall(text):
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower().strip(".")
            if not host or host in _LOCAL_HINTS or host in allowed:
                continue
            hosts.add(host)
    return hosts


def _unsafe_schemes(texts: list[str]) -> list[str]:
    hits = []
    for text in texts:
        match = UNSAFE_SCHEME_RE.search(text)
        if match:
            hits.append(match.group(0).rstrip(":"))
    return list(dict.fromkeys(hits))


def _unsafe_html_attrs(html: str) -> list[str]:
    hits = []
    for match in ATTR_URL_RE.finditer(html):
        value = match.group(1).strip()
        parsed = urlparse(value)
        if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
            hits.append(f"{parsed.scheme} (href/src {value[:40]!r})")
    return hits


def check(guide: Guide, html_out: str, source_url: str,
          max_external_links: int = 6) -> list[str]:
    """Return a list of guard violations (empty means the guide is safe)."""
    problems: list[str] = []
    texts = _prose_fields(guide)

    for scheme in _unsafe_schemes(texts):
        problems.append(f"unsafe scheme '{scheme}' in guide content")

    allowed = set(TEMPLATE_HOSTS)
    if source_url:
        host = urlparse(source_url).hostname
        if host:
            allowed.add(host.lower().strip("."))
    external = _external_hosts(texts, allowed)
    if len(external) > max_external_links:
        problems.append(
            f"too many external hosts ({len(external)} > {max_external_links}): "
            f"{', '.join(sorted(external))}"
        )

    for hit in _unsafe_html_attrs(html_out):
        problems.append(f"unsafe html attribute: {hit}")

    if problems:
        logger.warning("content_guard_violations count=%d", len(problems))
    return problems

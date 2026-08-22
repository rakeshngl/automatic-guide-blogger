import json
import logging

from curl_cffi import requests as impersonate_requests

from guides_writer.sources.base import CandidateItem, SourceError

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://www.producthunt.com/v2/api/graphql"

QUERY = """
query {
  posts(first: %(limit)d, order: RANKING) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        website
        votesCount
        createdAt
        topics(first: 3) { edges { node { name } } }
      }
    }
  }
}
"""


def _build_query(limit: int) -> str:
    return QUERY % {"limit": limit}


def parse_posts(payload: dict, limit: int = 10) -> list[CandidateItem]:
    try:
        edges = payload["data"]["posts"]["edges"]
    except (KeyError, TypeError) as exc:
        raise SourceError(f"unexpected Product Hunt payload: {json.dumps(payload)[:300]}") from exc
    items: list[CandidateItem] = []
    for i, edge in enumerate(edges[:limit], start=1):
        node = edge["node"]
        topics = [t["node"]["name"] for t in node.get("topics", {}).get("edges", [])]
        items.append(
            CandidateItem(
                source="producthunt",
                title=node["name"],
                url=node.get("website") or node.get("url") or "",
                tagline=node.get("tagline", ""),
                metrics={
                    "votes": node.get("votesCount", 0),
                    "ph_url": node.get("url"),
                    "created_at": node.get("createdAt"),
                },
                topics=topics,
                rank=i,
            )
        )
    return items


class ProductHuntAdapter:
    name = "producthunt"

    def __init__(self, developer_token: str, limit: int = 10):
        if not developer_token:
            raise SourceError("Product Hunt developer token missing")
        self._token = developer_token
        self._limit = limit

    def fetch(self) -> list[CandidateItem]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = impersonate_requests.post(
            GRAPHQL_URL,
            json={"query": _build_query(self._limit)},
            headers=headers,
            impersonate="chrome",
            timeout=30,
        )
        if resp.status_code != 200:
            raise SourceError(f"Product Hunt API HTTP {resp.status_code}: {resp.text[:200]}")
        items = parse_posts(resp.json(), limit=self._limit)
        logger.info("producthunt_fetch_ok count=%d", len(items))
        return items

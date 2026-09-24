from __future__ import annotations

from urllib.parse import urlsplit

from ddgs import DDGS


def search_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    query = " ".join(query.split())[:320]
    if not query:
        return []

    results: list[dict[str, str]] = []
    for result in DDGS(timeout=15).text(query, max_results=limit, backend="auto"):
        title = " ".join(str(result.get("title") or "").split())[:240]
        url = str(result.get("href") or "").strip()
        snippet = " ".join(str(result.get("body") or "").split())[:700]
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if not title and not snippet:
            continue
        results.append(
            {
                "title": title,
                "url": url[:1000],
                "snippet": snippet,
                "domain": parsed.hostname.casefold(),
            }
        )
        if len(results) >= limit:
            break
    return results

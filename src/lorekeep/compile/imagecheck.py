"""Verify that extracted ``props.image_links`` actually resolve.

The schema tells the model to record only links that really open, but an
extractor reading text cannot check that. This module is the other half of the
contract: after resolve, every candidate URL is fetched and any link that is not
a live image is dropped before it reaches ``facts.jsonl``.

Results are cached per URL (not per chunk) so recompiles do not re-fetch, and a
compile without network access degrades to "keep the links, flag nothing"
instead of failing.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from lorekeep.models import Node

log = logging.getLogger("lorekeep")

IMAGE_PROP = "image_links"

# Vietnamese news CDNs answer 403 to non-browser agents, so a plain urllib
# request would report every one of them as dead.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_PROBE_BYTES = 2047


@dataclass
class ImageCheckStats:
    checked: int = 0
    alive: int = 0
    dead: int = 0
    cached: int = 0
    nodes_changed: int = 0
    dead_urls: dict[str, str] = field(default_factory=dict)   # url -> reason


def _as_list(value: Any) -> list[str]:
    """Normalize the prop: models sometimes emit a bare string, sometimes junk."""
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)
                and v.startswith(("http://", "https://"))]
    return []


def probe(url: str, timeout: float) -> tuple[bool, str]:
    """Return ``(alive, reason)`` for one URL. Never raises."""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Range": f"bytes=0-{_PROBE_BYTES}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if resp.status not in (200, 206):
                return False, f"HTTP {resp.status}"
            # A 200 serving HTML is a soft 404: the CDN swapped in an error page
            # rather than returning an error status.
            if "image" not in ctype:
                return False, f"not an image: {ctype or 'unknown'}"
            return True, ""
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:                 # timeout, DNS, TLS, redirect loop
        return False, type(exc).__name__


class UrlCache:
    """Persistent url -> alive map. Dead entries are re-probed, live ones are not.

    A link that works is treated as settled; a link that failed may have been a
    transient network problem, so it gets another chance on the next compile.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, bool] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._data = {k: bool(v) for k, v in raw.items()}
            except Exception:
                log.warning("image link cache unreadable — starting fresh",
                            extra={"event": "imagecheck.cache_reset"})

    def get(self, url: str) -> bool | None:
        return self._data.get(url) or None      # False is not a cache hit

    def set(self, url: str, alive: bool) -> None:
        self._data[url] = alive

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("could not save image link cache error_type=%s",
                        type(exc).__name__,
                        extra={"event": "imagecheck.cache_save_failed"})


def collect_urls(nodes: Iterable[Node]) -> list[str]:
    """Every distinct http(s) URL sitting in props.image_links, in stable order."""
    seen: dict[str, None] = {}
    for node in nodes:
        for url in _as_list(node.props.get(IMAGE_PROP)):
            seen.setdefault(url, None)
    return list(seen)


def verify_nodes(
    nodes: Sequence[Node],
    cache_path: Path,
    *,
    timeout: float = 10.0,
    max_workers: int = 8,
) -> tuple[list[Node], ImageCheckStats]:
    """Drop dead ``image_links`` from ``nodes``. Returns new nodes plus stats.

    Nodes are frozen, so survivors are rebuilt with ``model_copy``. A node whose
    links all fail loses the prop entirely rather than keeping an empty list.
    """
    stats = ImageCheckStats()
    urls = collect_urls(nodes)
    if not urls:
        return list(nodes), stats

    cache = UrlCache(cache_path)
    verdicts: dict[str, bool] = {}
    to_probe: list[str] = []
    for url in urls:
        hit = cache.get(url)
        if hit is None:
            to_probe.append(url)
        else:
            verdicts[url] = hit
            stats.cached += 1

    if to_probe:
        workers = max(1, min(max_workers, len(to_probe)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for url, (alive, reason) in zip(
                to_probe, pool.map(lambda u: probe(u, timeout), to_probe)
            ):
                verdicts[url] = alive
                cache.set(url, alive)
                if not alive:
                    stats.dead_urls[url] = reason
        cache.save()

    stats.checked = len(urls)
    stats.alive = sum(1 for v in verdicts.values() if v)
    stats.dead = stats.checked - stats.alive

    out: list[Node] = []
    for node in nodes:
        raw = node.props.get(IMAGE_PROP)
        if raw is None:
            out.append(node)
            continue
        links = _as_list(raw)
        kept = [u for u in links if verdicts.get(u)]
        if kept == links and isinstance(raw, list):
            out.append(node)
            continue
        props = dict(node.props)
        if kept:
            props[IMAGE_PROP] = kept
        else:
            props.pop(IMAGE_PROP, None)
        out.append(node.model_copy(update={"props": props}))
        stats.nodes_changed += 1

    log.info(
        "image links verified checked=%s alive=%s dead=%s cached=%s nodes_changed=%s",
        stats.checked, stats.alive, stats.dead, stats.cached, stats.nodes_changed,
        extra={"event": "imagecheck.complete"},
    )
    for url, reason in stats.dead_urls.items():
        log.info("dropped dead image link reason=%s", reason,
                 extra={"event": "imagecheck.dead_link"})
    return out, stats

"""Dead image links must never reach facts.jsonl.

Every test stubs ``probe``: the suite is offline by contract, and the network
behaviour we care about (soft 404s, timeouts) is easier to state as a stub than
to provoke for real.
"""
from __future__ import annotations

import json

import pytest

from lorekeep.compile import imagecheck
from lorekeep.compile.imagecheck import (
    IMAGE_PROP,
    UrlCache,
    collect_urls,
    verify_nodes,
)
from lorekeep.models import Node

LIVE = "https://cdn.example.com/photo.jpg"
LIVE2 = "https://cdn.example.com/plant.png"
DEAD = "https://cdn.example.com/gone.jpg"


def node(node_id: str, links=None, node_type: str = "project") -> Node:
    props = {"name": node_id}
    if links is not None:
        props[IMAGE_PROP] = links
    return Node(id=node_id, type=node_type, ns=("vsf",), props=props)


@pytest.fixture
def stub_probe(monkeypatch):
    """Everything but DEAD resolves."""
    calls: list[str] = []

    def fake(url: str, timeout: float) -> tuple[bool, str]:
        calls.append(url)
        return (False, "HTTP 404") if url == DEAD else (True, "")

    monkeypatch.setattr(imagecheck, "probe", fake)
    return calls


def test_collect_urls_dedups_and_keeps_order():
    nodes = [node("prj:a", [LIVE, LIVE2]), node("prj:b", [LIVE2, LIVE])]
    assert collect_urls(nodes) == [LIVE, LIVE2]


def test_ignores_non_http_values():
    """Models sometimes answer with prose instead of a URL list."""
    assert collect_urls([node("prj:a", "khong co anh")]) == []
    assert collect_urls([node("prj:a", ["ftp://x/y.jpg", 42, None])]) == []


def test_bare_string_url_is_accepted():
    assert collect_urls([node("prj:a", LIVE)]) == [LIVE]


def test_dead_link_is_dropped_live_one_kept(tmp_path, stub_probe):
    out, stats = verify_nodes([node("prj:a", [LIVE, DEAD])], tmp_path / "c.json")

    assert out[0].props[IMAGE_PROP] == [LIVE]
    assert (stats.checked, stats.alive, stats.dead) == (2, 1, 1)
    assert stats.nodes_changed == 1
    assert stats.dead_urls == {DEAD: "HTTP 404"}


def test_prop_removed_when_every_link_dies(tmp_path, stub_probe):
    out, _ = verify_nodes([node("prj:a", [DEAD])], tmp_path / "c.json")

    assert IMAGE_PROP not in out[0].props
    assert out[0].props["name"] == "prj:a"


def test_untouched_node_is_the_same_object(tmp_path, stub_probe):
    """No image_links means nothing to rewrite."""
    original = node("prj:a")
    out, stats = verify_nodes([original], tmp_path / "c.json")

    assert out[0] is original
    assert stats.checked == 0
    assert not (tmp_path / "c.json").exists(), "no probe, no cache file"


def test_live_verdict_is_cached_across_runs(tmp_path, stub_probe):
    cache = tmp_path / "c.json"
    verify_nodes([node("prj:a", [LIVE])], cache)
    assert stub_probe == [LIVE]

    _, stats = verify_nodes([node("prj:b", [LIVE])], cache)

    assert stub_probe == [LIVE], "a live link must not be re-fetched"
    assert stats.cached == 1


def test_dead_verdict_is_retried(tmp_path, stub_probe):
    """A failure may have been transient, so it gets another chance."""
    cache = tmp_path / "c.json"
    verify_nodes([node("prj:a", [DEAD])], cache)
    verify_nodes([node("prj:a", [DEAD])], cache)

    assert stub_probe == [DEAD, DEAD]


def test_cache_survives_a_corrupt_file(tmp_path, stub_probe):
    cache = tmp_path / "c.json"
    cache.write_text("{not json", encoding="utf-8")

    out, _ = verify_nodes([node("prj:a", [LIVE])], cache)

    assert out[0].props[IMAGE_PROP] == [LIVE]
    assert json.loads(cache.read_text())[LIVE] is True


def test_urlcache_false_is_not_a_hit(tmp_path):
    cache = UrlCache(tmp_path / "c.json")
    cache.set(DEAD, False)
    cache.set(LIVE, True)

    assert cache.get(DEAD) is None
    assert cache.get(LIVE) is True

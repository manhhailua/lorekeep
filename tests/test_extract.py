from datetime import date
import json
import pytest
from lorekeep.models import DocChunk, Schema
from lorekeep.compile.extract import build_prompt, build_system_prompt, parse_response, SYSTEM_PROMPT_BASE


SCHEMA = Schema.load({
    "version": 1,
    "node_types": {"service": {"props": {"name": "string", "lang": "string"}},
                   "team": {"props": {"name": "string"}},
                   "decision": {"props": {"title": "string"}}},
    "edge_types": {"depends_on": {"from": "service", "to": "service"},
                   "decided_by": {"from": "decision", "to": "team"}},
})


def make_chunk(text="x"):
    return DocChunk(path="raw/backend/a.md", start_line=3, end_line=3,
                    text=text, namespace="teams/backend")


def test_system_prompt_contains_schema():
    s = build_system_prompt(SCHEMA)
    assert "service" in s and "depends_on" in s
    assert "name:string" in s and "lang:string" in s
    assert "knowledge-graph extractor" in s


def test_system_prompt_scopes_temporal_dates_to_exact_fact():
    s = build_system_prompt(SCHEMA)
    assert "set dates on that edge only" in s
    assert "entity itself ceased to exist" in s
    assert "service launched on D" in s
    assert "dependency removed on D" in s


def test_system_prompt_has_altitude_rule():
    s = build_system_prompt(SCHEMA)
    assert "Altitude rule" in s
    # low-altitude tokens must not become nodes
    assert "must NOT become nodes" in s


def test_system_prompt_requires_grounded_human_readable_content():
    s = build_system_prompt(SCHEMA)
    assert "props.summary" in s
    assert "Every edge must have props.description" in s
    assert "same language as the source chunk" in s
    assert "do not invent details" in s


def test_me_namespace_adds_subject_prompt():
    s_me = build_system_prompt(SCHEMA, ns="me")
    s_team = build_system_prompt(SCHEMA, ns="backend")
    assert "personal profile" in s_me           # subject-centric guidance
    assert "ONE canonical person" in s_me
    assert "personal profile" not in s_team     # team ns stays entity-centric


def test_team_namespace_omits_subject_prompt():
    s = build_system_prompt(SCHEMA, ns="teams/backend")
    assert "personal profile" not in s
    assert "Altitude rule" in s                 # altitude applies everywhere


def test_configured_private_namespace_gets_subject_prompt():
    s_private = build_system_prompt(SCHEMA, ns="private", personal_ns="private")
    s_me = build_system_prompt(SCHEMA, ns="me", personal_ns="private")
    assert "personal profile" in s_private
    assert "personal profile" not in s_me


def test_user_prompt_is_chunk_text_only():
    c = make_chunk("The payments-api is a Go service.")
    p = build_prompt(c, SCHEMA)
    assert p == "The payments-api is a Go service."


def test_parse_response_maps_nodes_and_edges():
    c = make_chunk()
    raw = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth"},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
        ],
        "aliases": {"payments-api": ["payments-api"]},
    })
    nodes, edges, aliases = parse_response(raw, c)
    assert len(nodes) == 2
    assert nodes[0].id == "svc:payments-api"
    assert nodes[0].ns == ("teams/backend",)
    assert nodes[0].src == ("raw/backend/a.md:3",)
    assert nodes[0].valid_from == date(2024, 1, 15)
    assert len(edges) == 1
    assert edges[0].from_ == "svc:payments-api"
    assert edges[0].valid_to == date(2025, 3, 1)
    assert aliases == {"payments-api": ["payments-api"]}


def test_parse_response_preserves_human_fields_and_edge_props():
    c = make_chunk("Dịch vụ thanh toán phụ thuộc xác thực để kiểm tra token.")
    raw = json.dumps({
        "nodes": [{
            "id": "svc:payments",
            "type": "service",
            "title": "Thanh toán",
            "summary": "Xử lý giao dịch thanh toán.",
            "description": "Dịch vụ lõi cho luồng thanh toán.",
        }],
        "edges": [{
            "type": "depends_on",
            "from": "svc:payments",
            "to": "svc:auth",
            "description": "Dùng auth để kiểm tra token.",
            "props": {"criticality": "high"},
        }],
        "aliases": {},
    })

    nodes, edges, _ = parse_response(raw, c)

    assert nodes[0].props == {
        "title": "Thanh toán",
        "summary": "Xử lý giao dịch thanh toán.",
        "description": "Dịch vụ lõi cho luồng thanh toán.",
    }
    assert edges[0].props == {
        "criticality": "high",
        "description": "Dùng auth để kiểm tra token.",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-24T02:01:12+00:00", date(2026, 7, 24)),
        ("2026-07-24T23:59:59-05:00", date(2026, 7, 24)),
        ("2026-07-24T02:01:12Z", date(2026, 7, 24)),
    ],
)
def test_parse_response_accepts_iso_datetime_dates(value, expected):
    c = make_chunk()
    raw = json.dumps({
        "nodes": [{
            "id": "svc:payments-api",
            "type": "service",
            "name": "payments-api",
            "valid_from": value,
        }],
        "edges": [],
    })

    nodes, _, _ = parse_response(raw, c, schema=SCHEMA)

    assert nodes[0].valid_from == expected


def test_parse_response_rejects_invalid_iso_datetime():
    c = make_chunk()
    raw = json.dumps({
        "nodes": [{
            "id": "svc:payments-api",
            "type": "service",
            "name": "payments-api",
            "valid_from": "2026-07-24Tnot-a-time",
        }],
        "edges": [],
    })

    with pytest.raises(ValueError, match="Invalid isoformat string"):
        parse_response(raw, c, schema=SCHEMA)


def test_parse_response_skips_invalid_node_type():
    c = make_chunk()
    raw = json.dumps({"nodes": [{"id": "x", "type": "bogus", "name": "x"}], "edges": []})
    nodes, edges, aliases = parse_response(raw, c, schema=SCHEMA)
    assert nodes == []


from pathlib import Path
from lorekeep.compile.extract import ExtractionCache, extract_chunk
from lorekeep.compile.providers import FakeProvider


def test_cache_key_depends_on_chunk_and_schema(tmp_path: Path):
    cache = ExtractionCache(tmp_path / "cache.json")
    c = make_chunk("hello")
    k1 = cache.key(c, SCHEMA.version)
    c2 = make_chunk("hello")
    assert cache.key(c2, SCHEMA.version) == k1
    c3 = make_chunk("different")
    assert cache.key(c3, SCHEMA.version) != k1


def test_extract_chunk_caches_and_reuses(tmp_path: Path):
    cache = ExtractionCache(tmp_path / "cache.json")
    c = make_chunk("The payments-api is a Go service.")
    raw = json.dumps({"nodes": [{"id": "svc:payments-api", "type": "service",
                                  "name": "payments-api", "props": {"lang": "go"}}],
                      "edges": [], "aliases": {}})
    provider = FakeProvider(responses=[raw])          # only ONE response available
    n1, e1, a1 = extract_chunk(c, SCHEMA, provider, cache)
    assert len(n1) == 1
    # second call must hit cache, not the provider (would raise if it called)
    n2, e2, a2 = extract_chunk(c, SCHEMA, provider, cache)
    assert len(n2) == 1
    assert len(provider.calls) == 1                    # provider called once


def test_extract_chunk_cache_invalidates_when_system_prompt_changes(
    tmp_path: Path, monkeypatch,
):
    import lorekeep.compile.extract as extract_module

    cache = ExtractionCache(tmp_path / "cache.json")
    c = make_chunk("The payments-api is a Go service.")
    raw = json.dumps({"nodes": [], "edges": [], "aliases": {}})
    provider = FakeProvider(responses=[raw, raw])

    extract_chunk(c, SCHEMA, provider, cache)
    monkeypatch.setattr(
        extract_module,
        "TEMPORAL_RULE",
        extract_module.TEMPORAL_RULE + " Updated guidance.",
    )
    extract_chunk(c, SCHEMA, provider, cache)

    assert len(provider.calls) == 2


def test_cache_persists_to_disk(tmp_path: Path):
    p = tmp_path / "cache.json"
    cache = ExtractionCache(p)
    c = make_chunk("x")
    raw = json.dumps({"nodes": [], "edges": [], "aliases": {}})
    extract_chunk(c, SCHEMA, FakeProvider([raw]), cache)
    cache.save()
    assert p.exists()


def test_parse_response_tolerates_fenced_json():
    c = make_chunk()
    payload = {"nodes": [{"id": "svc:a", "type": "service", "name": "a"}],
               "edges": [], "aliases": {}}
    raw = "```json\n" + json.dumps(payload) + "\n```"
    nodes, _, _ = parse_response(raw, c, schema=SCHEMA)
    assert len(nodes) == 1 and nodes[0].id == "svc:a"


def test_parse_response_tolerates_prose_wrapped_json():
    c = make_chunk()
    payload = {"nodes": [{"id": "svc:b", "type": "service", "name": "b"}],
               "edges": [], "aliases": {}}
    raw = "Here is the result:\n" + json.dumps(payload) + "\nThanks."
    nodes, _, _ = parse_response(raw, c, schema=SCHEMA)
    assert len(nodes) == 1 and nodes[0].id == "svc:b"


def test_parse_response_raises_on_non_json():
    c = make_chunk()
    with pytest.raises(ValueError):
        parse_response("no JSON here at all", c, schema=SCHEMA)

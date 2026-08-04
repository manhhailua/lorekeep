import json
from pathlib import Path
from lorekeep.pipeline import compile_graph, measure_content_quality
from lorekeep.compile.providers import FakeProvider
from lorekeep.models import Schema


def copy_fixture(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text())


def test_compile_pipeline_produces_facts(tmp_path: Path, fixtures: Path):
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/backend/payments.md",
                 raw / "teams/backend/payments.md")
    out = tmp_path / "graph"
    cache = tmp_path / "cache.json"
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    canned = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "summary": "Main API for payment requests.",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth",
             "summary": "Validates service credentials."},
            {"id": "team:backend", "type": "team", "name": "team-backend",
             "summary": "Backend engineering team."},
            {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
             "summary": "Adopts internal request signing.",
             "props": {"title": "payments-api adopts internal signing"}},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "description": "Uses auth to validate incoming credentials.",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
            {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend",
             "description": "The backend team approved the signing decision."},
        ],
        "aliases": {},
    })
    provider = FakeProvider([canned])

    manifest = compile_graph(raw_root=raw, out_dir=out, schema=schema,
                             provider=provider, cache_path=cache, chunk_lines=60)
    facts = (out / "facts.jsonl").read_text().splitlines()
    assert len(facts) == 6                       # 4 nodes + 2 edges
    assert (out / "manifest.json").exists()
    assert manifest.node_count == 4
    assert manifest.edge_count == 2
    assert manifest.content_quality is not None
    assert manifest.content_quality.node_summary_coverage == 1.0
    assert manifest.content_quality.edge_description_coverage == 1.0


def test_content_quality_reports_generic_edges_and_duplicate_labels():
    from lorekeep.models import Edge, Node

    schema = Schema.load({
        "version": 1,
        "node_types": {"service": {"props": {"name": "string"}}},
        "edge_types": {"relates_to": {"from": "service", "to": "service"}},
    })
    nodes = [
        Node(id="svc:a", type="service", ns=("team",), props={"name": "API", "summary": "First."}),
        Node(id="svc:b", type="service", ns=("team",), props={"name": "API"}),
    ]
    edges = [
        Edge(id="e1", type="relates_to", from_="svc:a", to="svc:b", ns=("team",)),
    ]

    quality = measure_content_quality(nodes, edges, schema)

    assert quality.node_label_coverage == 1.0
    assert quality.node_summary_coverage == 0.5
    assert quality.edge_description_coverage == 0.0
    assert quality.generic_edge_ratio == 1.0
    assert quality.duplicate_label_count == 1


def test_pipeline_per_chunk_failure_logs_exception(tmp_path: Path, fixtures: Path, caplog):
    """A per-chunk failure must be logged (full traceback) for daemon/verbose
    debugging, while the manifest still records the short message."""
    import logging as _logging
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/backend/payments.md",
                 raw / "teams/backend/payments.md")
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    class _Boom(FakeProvider):
        def extract_json(self, system, user):
            raise RuntimeError("LLM Provider NOT provided")

    with caplog.at_level(_logging.ERROR, logger="lorekeep"):
        manifest = compile_graph(raw_root=raw, out_dir=tmp_path / "graph",
                                 schema=schema, provider=_Boom(responses=[]),
                                 cache_path=tmp_path / "cache.json", chunk_lines=60)
    assert manifest.node_count == 0
    assert manifest.errors                      # short message preserved
    assert any("compile: chunk failed" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records)  # traceback attached

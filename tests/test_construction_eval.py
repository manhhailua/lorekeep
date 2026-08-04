from pathlib import Path
from lorekeep.eval.gold import load_gold, load_compiled, node_key, edge_key


def test_load_gold(tmp_path: Path, fixtures: Path):
    facts = load_gold(fixtures / "gold")
    ids = {f.id for f in facts}
    assert "svc:payments-api" in ids
    assert len(facts) == 6


def test_gold_corpus_is_fully_human_readable(fixtures: Path):
    from lorekeep.models import Edge, Node

    facts = load_gold(fixtures / "gold")
    nodes = [fact for fact in facts if isinstance(fact, Node)]
    edges = [fact for fact in facts if isinstance(fact, Edge)]

    assert all(
        str(node.props.get("name") or node.props.get("title") or "").strip()
        for node in nodes
    )
    assert all(str(node.props.get("summary") or "").strip() for node in nodes)
    assert all(str(edge.props.get("description") or "").strip() for edge in edges)


def test_node_key_uses_type_and_name():
    from lorekeep.models import Node
    n = Node(id="svc:x", type="service", ns=("t/b",), props={"name": "auth"})
    assert node_key(n) == ("service", "auth")


def test_edge_key_uses_type_and_endpoint_names():
    from lorekeep.models import Node, Edge
    nodes = {"svc:a": Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
             "svc:b": Node(id="svc:b", type="service", ns=("t/b",), props={"name": "b"})}
    e = Edge(id="e1", type="depends_on", **{"from": "svc:a"}, to="svc:b", ns=("t/b",))
    assert edge_key(e, nodes) == ("depends_on", "a", "b")


from lorekeep.eval.construction import precision_recall_f1, extraction_report


def test_prf1_perfect():
    p, r, f1 = precision_recall_f1({1, 2, 3}, {1, 2, 3})
    assert (p, r, f1) == (1.0, 1.0, 1.0)


def test_prf1_partial():
    p, r, f1 = precision_recall_f1({1, 2, 3}, {2, 3, 4})
    assert p == 2/3 and r == 2/3 and abs(f1 - 2/3) < 1e-9


def test_extraction_report_against_gold(tmp_path: Path, fixtures: Path):
    # compile with the canned fixture response, then score vs gold
    import json as _json
    from lorekeep.pipeline import compile_graph
    from lorekeep.compile.providers import FakeProvider
    from lorekeep.models import Schema
    from lorekeep.eval.gold import load_gold

    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/payments.md").write_text(
        (fixtures / "raw/backend/payments.md").read_text())
    schema = Schema.load(_json.loads((fixtures / "schema.json").read_text()))
    canned = _json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth"},
            {"id": "team:backend", "type": "team", "name": "team-backend"},
            {"id": "dec:adr-007", "type": "decision",
             "props": {"title": "payments-api adopts internal signing"}},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
            {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
        ],
        "aliases": {},
    })
    compile_graph(raw, tmp_path / "g", schema, FakeProvider([canned]), tmp_path / "c.json")
    report = extraction_report(tmp_path / "g", fixtures / "gold")
    assert report["nodes"]["f1"] == 1.0
    assert report["edges"]["f1"] == 1.0


from lorekeep.eval.construction import entity_resolution_f1


def test_er_f1_perfect_merge():
    # two distinct mentions correctly merged under one canonical id
    from lorekeep.models import Node
    compiled = [Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
                Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a2"})]
    gold = [{"id": "svc:a", "aliases": ["a", "a2"]}]
    r = entity_resolution_f1(compiled, gold)
    assert r["f1"] == 1.0


def test_er_f1_false_split():
    # gold says one entity, compiled split into two -> recall drops
    from lorekeep.models import Node
    compiled = [Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
                Node(id="svc:b", type="service", ns=("t/b",), props={"name": "b"})]
    gold = [{"id": "svc:x", "aliases": ["a", "b"]}]
    r = entity_resolution_f1(compiled, gold)
    assert r["recall"] < 1.0


from lorekeep.eval.construction import structure_report


def test_structure_metrics(tmp_path: Path, fixtures: Path):
    report = structure_report(fixtures / "gold")
    assert report["node_count"] == 4
    assert report["edge_count"] == 2
    assert report["dangling_edge_rate"] == 0.0
    assert report["avg_degree"] == 0.5            # 2 edges / 4 nodes

"""Unit tests for merge_journals confidence gating (auto / flag / quarantine)."""
from lorekeep.compile.resolve import merge_journals, JournalMergeResult
from lorekeep.models import JournalEntry, Node, Edge


def _node(id="svc:a", type="service"):
    return Node(id=id, type=type, ns=("backend",), props={"name": id})


def _edge(frm="svc:a", to="svc:b", etype="depends_on"):
    return Edge(id="e1", type=etype, **{"from": frm}, to=to, ns=("backend",))


def _entry(fact_dict, confidence, status="pending"):
    return JournalEntry(
        fact=fact_dict,
        agent="test",
        ns="backend",
        confidence=confidence,
        proposed_at="2026-06-28T00:00:00Z",
        status=status,
    )


def _node_fact(id="svc:new", type="service", props=None):
    return {
        "kind": "node",
        "id": id,
        "type": type,
        "ns": ["backend"],
        "props": props or {"name": id},
        "src": [],
    }


def _edge_fact(frm="svc:a", to="svc:b", etype="depends_on"):
    return {
        "kind": "edge",
        "id": "",
        "type": etype,
        "from": frm,
        "to": to,
        "ns": ["backend"],
        "props": {},
        "src": [],
    }


# ── Confidence thresholds ─────────────────────────────────────────────────


def test_high_confidence_auto_merged():
    nodes = [_node()]
    entry = _entry(_node_fact(id="svc:new"), confidence=0.9)
    r = merge_journals(nodes, [], [entry])
    assert len(r.merged) == 1
    assert len(r.flagged) == 0
    assert len(r.quarantined) == 0
    assert any(n.id == "svc:new" for n in r.nodes)


def test_medium_confidence_merged_and_flagged():
    nodes = [_node()]
    entry = _entry(_node_fact(id="svc:new"), confidence=0.6)
    r = merge_journals(nodes, [], [entry])
    assert len(r.merged) == 0
    assert len(r.flagged) == 1
    assert len(r.quarantined) == 0
    assert any(n.id == "svc:new" for n in r.nodes)


def test_low_confidence_quarantined():
    nodes = [_node()]
    entry = _entry(_node_fact(id="svc:new"), confidence=0.3)
    r = merge_journals(nodes, [], [entry])
    assert len(r.merged) == 0
    assert len(r.flagged) == 0
    assert len(r.quarantined) == 1
    assert not any(n.id == "svc:new" for n in r.nodes)


def test_boundary_08_is_auto_merged():
    entry = _entry(_node_fact(), confidence=0.8)
    r = merge_journals([], [], [entry])
    assert len(r.merged) == 1
    assert len(r.flagged) == 0


def test_boundary_05_is_flagged():
    entry = _entry(_node_fact(), confidence=0.5)
    r = merge_journals([], [], [entry])
    assert len(r.merged) == 0
    assert len(r.flagged) == 1


def test_just_below_05_quarantined():
    entry = _entry(_node_fact(), confidence=0.49)
    r = merge_journals([], [], [entry])
    assert len(r.quarantined) == 1


# ── Non-pending status ────────────────────────────────────────────────────


def test_non_pending_entries_skipped():
    entry = _entry(_node_fact(), confidence=0.9, status="merged")
    r = merge_journals([], [], [entry])
    assert len(r.merged) == 0
    assert not any(n.id == "svc:new" for n in r.nodes)


# ── Invalid schema ────────────────────────────────────────────────────────


def test_invalid_fact_schema_quarantined():
    bad_fact = {"kind": "node", "id": "x", "type": "service", "ns": [], "props": {}, "src": []}
    bad_fact["valid_from"] = "not-a-date"  # fails Pydantic validation
    entry = _entry(bad_fact, confidence=0.9)
    r = merge_journals([], [], [entry])
    assert len(r.quarantined) == 1
    assert not any(n.id == "x" for n in r.nodes)


# ── Node prop merge ───────────────────────────────────────────────────────


def test_existing_node_props_merged():
    nodes = [_node(id="svc:a", type="service")]
    # Node constructor hardcodes props={"name": id}; override
    nodes[0] = Node(id="svc:a", type="service", ns=("backend",),
                    props={"name": "a", "lang": "go"})
    fact = _node_fact(id="svc:a", props={"lang": "rust", "version": "2"})
    entry = _entry(fact, confidence=0.9)
    r = merge_journals(nodes, [], [entry])
    merged = next(n for n in r.nodes if n.id == "svc:a")
    assert merged.props["lang"] == "rust"
    assert merged.props["name"] == "a"
    assert merged.props["version"] == "2"


# ── Edge dedup ────────────────────────────────────────────────────────────


def test_duplicate_edges_deduplicated():
    existing_edge = _edge()
    new_entry = _entry(_edge_fact(), confidence=0.9)
    r = merge_journals([], [existing_edge], [new_entry])
    assert len(r.edges) == 1


def test_journal_edge_gets_generated_id():
    entry = _entry(_edge_fact(), confidence=0.9)
    r = merge_journals([], [], [entry])
    assert len(r.edges) == 1
    assert r.edges[0].id != ""


# ── Mixed batch ───────────────────────────────────────────────────────────


def test_mixed_batch_auto_flag_quarantine():
    entries = [
        _entry(_node_fact(id="svc:auto"), confidence=0.9),
        _entry(_node_fact(id="svc:flagged"), confidence=0.6),
        _entry(_node_fact(id="svc:quarantined"), confidence=0.3),
    ]
    r = merge_journals([], [], entries)
    assert len(r.merged) == 1
    assert len(r.flagged) == 1
    assert len(r.quarantined) == 1
    assert r.merge_count == 1
    assert r.flagged_count == 1
    assert r.quarantine_count == 1

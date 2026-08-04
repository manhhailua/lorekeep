from datetime import date
from lorekeep.models import Node, Edge
from lorekeep.compile.resolve import resolve, ResolveResult
from lorekeep.defaults import DEFAULT_SCHEMA
from lorekeep.models import Schema


def n(id, type="service", name=None):
    return Node(id=id, type=type, ns=("teams/backend",),
                props={"name": name} if name else {})


def e(type="depends_on", frm="svc:a", to="svc:b"):
    return Edge(id="", type=type, **{"from": frm}, to=to, ns=("teams/backend",))


def test_dedup_merges_alias_to_canonical():
    nodes = [n("svc:payments-api", name="payments-api"), n("svc:pay", name="payments-api")]
    aliases = {"payments-api": ["payments-api", "pay"]}
    r = resolve(nodes, [], aliases)
    ids = {x.id for x in r.nodes}
    assert ids == {"svc:payments-api"}            # svc:pay merged away


def test_dedup_rewrites_edge_endpoints():
    nodes = [n("svc:a"), n("svc:a2")]
    edges = [e(frm="svc:a", to="svc:a2")]
    aliases = {"a": ["a", "a2"]}                  # map by name; but ids differ -> use explicit alias id map
    # Provide an explicit id-alias map by using name keys
    r = resolve(nodes, edges, aliases_map={"svc:a2": "svc:a"})
    assert all(x.from_ == "svc:a" and x.to == "svc:a" for x in r.edges)


def test_ghost_canonical_does_not_falsely_quarantine():
    # explicit_map targets a canonical id that is NOT a node id; the merged node
    # must be normalized to that canonical id and referenced edges must survive.
    nodes = [n("svc:a"), n("svc:b")]
    edges = [e(frm="svc:a", to="svc:b")]
    r = resolve(nodes, edges, aliases_map={"svc:a": "svc:ghost", "svc:b": "svc:ghost"})
    # both collapse to one canonical node "svc:ghost"; the single edge a->b becomes
    # a self-loop on ghost and is quarantined (NOT falsely dangled).
    assert [x.id for x in r.nodes] == ["svc:ghost"]
    assert r.edges == []
    assert r.quarantined and r.quarantined[0][1] == "self-loop"


def test_dangling_edge_is_quarantined():
    nodes = [n("svc:a")]
    edges = [e(frm="svc:a", to="svc:ghost")]
    r = resolve(nodes, edges)
    assert r.edges == []
    assert len(r.quarantined) == 1
    assert "dangling" in r.quarantined[0][1]


def test_self_loop_is_quarantined():
    nodes = [n("svc:a")]
    edges = [e(frm="svc:a", to="svc:a")]
    r = resolve(nodes, edges)
    assert r.edges == []
    assert r.quarantined[0][1] == "self-loop"


def test_edge_ids_are_deterministic():
    nodes = [n("svc:a"), n("svc:b"), n("svc:c")]
    edges = [e(frm="svc:a", to="svc:b"), e(frm="svc:b", to="svc:c")]
    r = resolve(nodes, edges)
    assert [x.id for x in r.edges] == ["e_depends_on_0001", "e_depends_on_0002"]


def test_human_text_merge_is_rich_and_input_order_independent():
    terse = Node(
        id="svc:api", type="service", ns=("team",),
        props={"name": "API", "summary": "API service.", "description": "Handles API calls."},
        src=("team/a.md:1",),
    )
    rich = Node(
        id="svc:api", type="service", ns=("team",),
        props={
            "name": "API",
            "summary": "Core API service for customer payment requests.",
            "description": "Handles API calls.\n\nValidates requests before routing them.",
        },
        src=("team/b.md:1",),
    )

    forward = resolve([terse, rich], []).nodes[0]
    reverse = resolve([rich, terse], []).nodes[0]

    assert forward == reverse
    assert forward.props["summary"] == rich.props["summary"]
    assert forward.props["description"].count("Handles API calls.") == 1
    assert "Validates requests" in forward.props["description"]


def test_duplicate_logical_edges_coalesce_and_merge_descriptions():
    nodes = [n("svc:a"), n("svc:b")]
    edges = [
        e(frm="svc:a", to="svc:b").model_copy(update={
            "props": {"description": "Uses B for auth."},
            "src": ("team/a.md:1",),
        }),
        e(frm="svc:a", to="svc:b").model_copy(update={
            "props": {"description": "B validates access tokens."},
            "src": ("team/b.md:1",),
        }),
    ]

    result = resolve(nodes, list(reversed(edges)))

    assert len(result.edges) == 1
    assert result.edges[0].src == ("team/a.md:1", "team/b.md:1")
    assert "Uses B for auth." in result.edges[0].props["description"]
    assert "B validates access tokens." in result.edges[0].props["description"]


def test_alias_resolution_uses_title_and_canonical_surface_name():
    nodes = [
        Node(id="decision:adr-7", type="decision", ns=("team",), props={"title": "Adopt OAuth"}),
        Node(id="decision:oauth", type="decision", ns=("team",), props={"title": "OAuth decision"}),
    ]
    result = resolve(
        nodes, [], name_aliases={"Adopt OAuth": ["OAuth decision"]},
    )

    assert [node.id for node in result.nodes] == ["decision:adr-7"]


def test_schema_quarantines_invalid_edge_endpoint_types():
    nodes = [n("person:a", type="person"), n("goal:x", type="goal")]
    edges = [e(type="depends_on", frm="person:a", to="goal:x")]

    result = resolve(nodes, edges, schema=Schema.load(DEFAULT_SCHEMA))

    assert result.edges == []
    assert "invalid edge endpoints" in result.quarantined[0][1]


# ── auto-normalize (Phase 3) ───────────────────────────────────────────────
from lorekeep.compile.resolve import _normalize_id


class TestNormalizeId:
    def test_kebab_snake_space_collapse(self):
        assert _normalize_id("concept:context_purity") == "concept:context-purity"
        assert _normalize_id("concept:Context Purity") == "concept:context-purity"
        assert _normalize_id("concept:context-purity") == "concept:context-purity"

    def test_diacritics_preserved(self):
        # Vietnamese diacritics MUST stay — nguyễn != nguyen
        assert _normalize_id("person:nguyễn") == "person:nguyễn"
        assert _normalize_id("person:Nguyễn") == "person:nguyễn"

    def test_canonically_equivalent_unicode_collapses(self):
        assert _normalize_id("person:nguyễn") == _normalize_id(
            "person:nguye\u0302\u0303n"
        )

    def test_type_prefix_kept(self):
        assert _normalize_id("svc:payments-api").startswith("svc:")


class TestAutoMergeByNormalizedId:
    def test_merges_case_and_separator_variants(self):
        nodes = [
            n("domain:context-purity"),
            n("domain:context_purity"),
            n("domain:Context Purity"),
        ]
        result = resolve(nodes, [])
        assert len(result.nodes) == 1            # all 3 merged
        assert result.nodes[0].id == "domain:context-purity"

    def test_does_not_merge_different_diacritics(self):
        nodes = [n("person:nguyen"), n("person:nguyễn")]
        result = resolve(nodes, [])
        assert len(result.nodes) == 2            # distinct, kept

    def test_does_not_merge_different_types(self):
        nodes = [n("svc:auth", type="service"), n("domain:auth", type="domain")]
        result = resolve(nodes, [])
        assert len(result.nodes) == 2

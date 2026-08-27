"""Tests for orphan-node quarantine (#266): detect, review, and persistence.

Quarantine parks degree-0 nodes for human review instead of losing them
(hand-editing facts.jsonl is forbidden) or deleting their raw source blindly
(an orphan may be extraction noise or a fact that just never got linked).
The flag lives in ``props`` and must survive recompiles the same way
``props.merged_ids`` does — the exact bug class issue #281 warns about.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.agent import lint, self_heal
from lorekeep.cli import _load_prev_quarantine, app
from lorekeep.compile.writer import write_graph
from lorekeep.models import Edge, Manifest, Node
from lorekeep.pipeline import _apply_prev_quarantine
from lorekeep.store.graph import GraphStore, is_quarantined
from lorekeep.wiki import generate_wiki

runner = CliRunner()


def make_node(
    id: str, type: str = "service", ns: str = "test",
    props: dict | None = None, src: tuple = ("test.md:1",),
) -> Node:
    return Node(
        id=id, type=type, ns=(ns,), props=props or {"name": id},
        src=src, valid_from=date(2024, 1, 1),
    )


def make_edge(from_id: str, to_id: str, id: str = "e1", ns: str = "test") -> Edge:
    return Edge(
        id=id, type="depends_on", **{"from": from_id}, to=to_id, ns=(ns,),
        src=("test.md:1",), valid_from=date(2024, 1, 1),
    )


def _write_facts(out_dir: Path, nodes: list[Node], edges: list[Edge] | None = None) -> None:
    edges = edges or []
    write_graph(out_dir, nodes, edges, Manifest(
        schema_version=1, chunk_count=0,
        node_count=len(nodes), edge_count=len(edges),
        run_id="test", facts_hash="",
    ))


# ── is_quarantined ────────────────────────────────────────────────────────

class TestIsQuarantined:
    def test_false_by_default(self):
        assert not is_quarantined(make_node("svc:a"))

    def test_true_when_flag_set(self):
        n = make_node("svc:a", props={"name": "a", "quarantined_at": "2026-01-01"})
        assert is_quarantined(n)

    def test_false_when_flag_is_empty_string(self):
        n = make_node("svc:a", props={"name": "a", "quarantined_at": ""})
        assert not is_quarantined(n)


# agent.lint / self_heal exclude quarantined orphans

class TestLintExcludesQuarantined:
    def test_quarantined_orphan_not_reported(self):
        n = make_node("svc:a", props={"name": "a", "quarantined_at": "2026-01-01"})
        report = lint(GraphStore([n], []))
        assert report.orphans == []

    def test_unquarantined_orphan_still_reported(self):
        n = make_node("svc:a")
        report = lint(GraphStore([n], []))
        assert report.orphans == ["svc:a"]

    def test_connected_node_never_reported(self):
        n1, n2 = make_node("svc:a"), make_node("svc:b")
        report = lint(GraphStore([n1, n2], [make_edge("svc:a", "svc:b")]))
        assert report.orphans == []

    def test_dangling_edge_does_not_crash(self):
        """node_ids() includes the NetworkX phantom for svc:ghost; get_node
        KeyErrors on it. lint must return, and the phantom is not an orphan."""
        store = GraphStore([make_node("svc:a")], [make_edge("svc:a", "svc:ghost")])
        report = lint(store)
        assert "svc:ghost" not in report.orphans
        assert "svc:a" not in report.orphans


class TestSelfHealExcludesQuarantined:
    def test_quarantined_orphan_not_flagged(self):
        n = make_node("svc:a", props={"name": "a", "quarantined_at": "2026-01-01"})
        _, report = self_heal(GraphStore([n], []))
        assert [f for f in report.flagged if f["type"] == "orphan"] == []

    def test_unquarantined_orphan_still_flagged(self):
        n = make_node("svc:a")
        _, report = self_heal(GraphStore([n], []))
        orphan_flags = [f for f in report.flagged if f["type"] == "orphan"]
        assert len(orphan_flags) == 1


# pipeline._apply_prev_quarantine

class TestApplyPrevQuarantine:
    def test_reapplies_flag_onto_matching_fresh_node(self):
        """The core persistence contract: a freshly re-extracted node (no
        memory of the flag) gets it stamped back on by id."""
        fresh = make_node("svc:a")
        prev = {"svc:a": {"quarantined_at": "2026-01-01", "quarantined_reason": "orphan"}}
        result = _apply_prev_quarantine([fresh], prev)
        assert is_quarantined(result[0])
        assert result[0].props["quarantined_reason"] == "orphan"

    def test_noop_without_prev_quarantine(self):
        fresh = make_node("svc:a")
        result = _apply_prev_quarantine([fresh], None)
        assert result == [fresh]

    def test_does_not_touch_unrelated_node(self):
        fresh = make_node("svc:b")
        prev = {"svc:a": {"quarantined_at": "2026-01-01", "quarantined_reason": "orphan"}}
        result = _apply_prev_quarantine([fresh], prev)
        assert not is_quarantined(result[0])

    def test_does_not_override_an_already_set_flag(self):
        fresh = make_node("svc:a", props={
            "name": "a", "quarantined_at": "2026-06-01", "quarantined_reason": "manual",
        })
        prev = {"svc:a": {"quarantined_at": "2026-01-01", "quarantined_reason": "orphan"}}
        result = _apply_prev_quarantine([fresh], prev)
        assert result[0].props["quarantined_reason"] == "manual"


# cli._load_prev_quarantine

class TestLoadPrevQuarantine:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert _load_prev_quarantine(tmp_path / "facts.jsonl") == {}

    def test_reads_quarantined_node_props(self, tmp_path: Path):
        n = make_node("svc:a", props={
            "name": "a", "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        _write_facts(tmp_path, [n])
        prev = _load_prev_quarantine(tmp_path / "facts.jsonl")
        assert prev == {
            "svc:a": {"quarantined_at": "2026-01-01", "quarantined_reason": "orphan"},
        }

    def test_skips_non_quarantined_nodes(self, tmp_path: Path):
        _write_facts(tmp_path, [make_node("svc:a")])
        assert _load_prev_quarantine(tmp_path / "facts.jsonl") == {}


# wiki exclusion

class TestWikiExclusion:
    def test_quarantined_orphan_excluded_from_catalog_and_pages(self, tmp_path: Path):
        visible = make_node("svc:a")
        quarantined = make_node("svc:c", props={
            "name": "c", "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        graph = tmp_path / "graph"
        _write_facts(graph, [visible, quarantined])
        wiki = tmp_path / "wiki"
        result = generate_wiki(graph, wiki)

        assert result["pages"] == 1 + 4  # only the visible node gets a page
        assert not (wiki / "svc-c.md").exists()
        assert (wiki / "svc-a.md").exists()
        catalog = (wiki / "catalog.md").read_text()
        assert "svc-c" not in catalog
        overview = (wiki / "overview.md").read_text()
        assert "Orphan-quarantined nodes**: 1" in overview

    def test_quarantined_node_that_gained_an_edge_reappears(self, tmp_path: Path):
        """Safety net for the open question in #266: `lorekeep doctor`-style
        auto-recovery without a manual `quarantine review restore` step."""
        a = make_node("svc:a")
        c = make_node("svc:c", props={
            "name": "c", "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        graph = tmp_path / "graph"
        _write_facts(graph, [a, c], [make_edge("svc:a", "svc:c")])
        wiki = tmp_path / "wiki"
        generate_wiki(graph, wiki)

        assert (wiki / "svc-c.md").exists()
        assert "svc-c" in (wiki / "catalog.md").read_text()

    def test_no_quarantined_nodes_is_unaffected(self, tmp_path: Path):
        a, b = make_node("svc:a"), make_node("svc:b")
        graph = tmp_path / "graph"
        _write_facts(graph, [a, b], [make_edge("svc:a", "svc:b")])
        wiki = tmp_path / "wiki"
        result = generate_wiki(graph, wiki)
        assert result["pages"] == 2 + 4
        assert "Orphan-quarantined" not in (wiki / "overview.md").read_text()


# CLI: quarantine detect / review

class TestQuarantineDetectCli:
    def test_dry_run_lists_without_writing(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        _write_facts(home / "graph", [make_node("svc:a"), make_node("svc:b")])

        result = runner.invoke(app, ["quarantine", "detect"])
        assert result.exit_code == 0, result.output
        assert "svc:a" in result.output and "svc:b" in result.output
        assert "--apply" in result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        assert not any(is_quarantined(n) for n in store.all_nodes())

    def test_apply_writes_quarantine_flag(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        _write_facts(home / "graph", [make_node("svc:a")])

        result = runner.invoke(app, ["quarantine", "detect", "--apply"])
        assert result.exit_code == 0, result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        assert is_quarantined(store.get_node("svc:a"))

    def test_connected_node_is_not_a_candidate(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        a, b = make_node("svc:a"), make_node("svc:b")
        _write_facts(home / "graph", [a, b], [make_edge("svc:a", "svc:b")])

        result = runner.invoke(app, ["quarantine", "detect"])
        assert result.exit_code == 0, result.output
        assert "no orphaned nodes found" in result.output

    def test_no_graph_exits_nonzero(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("LOREKEEP_HOME", str(tmp_path / "home"))
        result = runner.invoke(app, ["quarantine", "detect"])
        assert result.exit_code == 1

    def test_apply_does_not_restamp_already_quarantined(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        parked = make_node("svc:a", props={
            "name": "a", "quarantined_at": "2026-01-01",
            "quarantined_reason": "manual",
        })
        _write_facts(home / "graph", [parked, make_node("svc:b")])

        result = runner.invoke(app, ["quarantine", "detect", "--apply"])
        assert result.exit_code == 0, result.output
        assert "svc:a" not in result.output
        assert "svc:b" in result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        a, b = store.get_node("svc:a"), store.get_node("svc:b")
        assert a.props["quarantined_at"] == "2026-01-01"
        assert a.props["quarantined_reason"] == "manual"
        assert is_quarantined(b)

    def test_apply_without_manifest_still_writes(self, monkeypatch, tmp_path: Path):
        """facts.jsonl can exist without manifest.json (hand-assembled graph,
        or a manifest lost to disk trouble) — `_write_quarantine_update` must
        not assume it's there."""
        home = tmp_path / "home"
        graph = home / "graph"
        graph.mkdir(parents=True)
        n = make_node("svc:a")
        (graph / "facts.jsonl").write_text(n.to_json_line() + "\n")
        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        result = runner.invoke(app, ["quarantine", "detect", "--apply"])
        assert result.exit_code == 0, result.output
        assert (graph / "manifest.json").exists()
        store = GraphStore.from_jsonl(graph / "facts.jsonl")
        assert is_quarantined(store.get_node("svc:a"))


class TestQuarantineReviewCli:
    def test_no_graph_exits_nonzero(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("LOREKEEP_HOME", str(tmp_path / "home"))
        result = runner.invoke(app, ["quarantine", "review"])
        assert result.exit_code == 1

    def test_shows_summary_when_present(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        n = make_node("svc:a", props={
            "name": "a", "summary": "Handles widget orders.",
            "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        _write_facts(home / "graph", [n])

        result = runner.invoke(app, ["quarantine", "review"], input="s\n")
        assert result.exit_code == 0, result.output
        assert "Handles widget orders." in result.output

    def test_restore_clears_flag(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        n = make_node("svc:a", props={
            "name": "a", "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        _write_facts(home / "graph", [n])

        result = runner.invoke(app, ["quarantine", "review"], input="r\n")
        assert result.exit_code == 0, result.output
        assert "restored: 1" in result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        assert not is_quarantined(store.get_node("svc:a"))

    def test_keep_preserves_flag(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        n = make_node("svc:a", props={
            "name": "a", "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        _write_facts(home / "graph", [n])

        result = runner.invoke(app, ["quarantine", "review"], input="k\n")
        assert result.exit_code == 0, result.output
        assert "kept quarantined: 1" in result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        assert is_quarantined(store.get_node("svc:a"))

    def test_skip_leaves_node_untouched(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        n = make_node("svc:a", props={
            "name": "a", "quarantined_at": "2026-01-01", "quarantined_reason": "orphan",
        })
        _write_facts(home / "graph", [n])

        result = runner.invoke(app, ["quarantine", "review"], input="s\n")
        assert result.exit_code == 0, result.output
        assert "skipped: 1" in result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        assert is_quarantined(store.get_node("svc:a"))

    def test_mixed_choices_restore_keep_skip(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        nodes = [
            make_node(nid, props={
                "name": nid, "quarantined_at": "2026-01-01",
                "quarantined_reason": "orphan",
            })
            for nid in ("svc:a", "svc:b", "svc:c")
        ]
        _write_facts(home / "graph", nodes)

        result = runner.invoke(app, ["quarantine", "review"], input="r\nk\ns\n")
        assert result.exit_code == 0, result.output
        assert "restored: 1" in result.output
        assert "kept quarantined: 1" in result.output
        assert "skipped: 1" in result.output

        store = GraphStore.from_jsonl(home / "graph" / "facts.jsonl")
        assert not is_quarantined(store.get_node("svc:a"))
        assert is_quarantined(store.get_node("svc:b"))
        assert is_quarantined(store.get_node("svc:c"))

    def test_nothing_quarantined_is_a_noop(self, monkeypatch, tmp_path: Path):
        home = tmp_path / "home"
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        _write_facts(home / "graph", [make_node("svc:a")])

        result = runner.invoke(app, ["quarantine", "review"])
        assert result.exit_code == 0, result.output
        assert "nothing quarantined" in result.output


# End-to-end: compile persists the flag (regression guard)

def test_compile_persists_quarantine_flag_across_recompile(
    patch_make_provider, monkeypatch, tmp_path: Path, fixtures: Path,
):
    """Without prev_quarantine wiring, `lorekeep compile` rebuilds every node
    fresh from raw/ and silently drops the flag — the exact failure mode
    issue #266 calls out for `props.merged_ids`-style persistence."""
    monkeypatch.setenv("LOREKEEP_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LOREKEEP_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    raw = tmp_path / "raw/backend/payments.md"
    raw.parent.mkdir(parents=True)
    raw.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output

    facts_path = tmp_path / "graph/facts.jsonl"
    store = GraphStore.from_jsonl(facts_path)
    assert store.get_node("svc:auth") is not None  # sanity: canned extraction

    quarantined_nodes = [
        n.model_copy(update={"props": {
            **n.props, "quarantined_at": "2026-01-01", "quarantined_reason": "manual",
        }}) if n.id == "svc:auth" else n
        for n in store.all_nodes()
    ]
    manifest = Manifest.from_json((tmp_path / "graph/manifest.json").read_text())
    write_graph(tmp_path / "graph", quarantined_nodes, store.all_edges(), manifest)

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.output

    store2 = GraphStore.from_jsonl(facts_path)
    auth = store2.get_node("svc:auth")
    assert auth is not None
    assert is_quarantined(auth), "quarantine flag must survive a full recompile from raw/"

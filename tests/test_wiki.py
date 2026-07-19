"""Tests for wiki generation from facts.jsonl."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lorekeep.models import Edge, Manifest, Node
from lorekeep.wiki import _slug, generate_wiki

runner = CliRunner()


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def sample_nodes() -> list[Node]:
    return [
        Node(
            id="svc:payments-api", type="service",
            ns=("backend",), valid_from=date(2024, 1, 15),
            props={"name": "payments-api", "lang": "go"},
            src=("raw/backend/payments.md:3",),
        ),
        Node(
            id="svc:auth", type="service",
            ns=("backend",),
            props={"name": "auth"},
            src=("raw/backend/payments.md:6",),
        ),
        Node(
            id="team:backend", type="team",
            ns=("backend",),
            props={"name": "team-backend"},
            src=("raw/backend/payments.md:3",),
        ),
        Node(
            id="dec:adr-007", type="decision",
            ns=("backend",),
            props={"title": "payments-api adopts internal signing"},
            src=("raw/backend/payments.md:11",),
        ),
    ]


@pytest.fixture
def sample_edges() -> list[Edge]:
    return [
        Edge(
            id="e_dep_1", type="depends_on",
            from_="svc:payments-api", to="svc:auth",
            ns=("backend",), valid_from=date(2024, 1, 15), valid_to=date(2025, 3, 1),
            src=("raw/backend/payments.md:6",),
        ),
        Edge(
            id="e_dec_1", type="decided_by",
            from_="dec:adr-007", to="team:backend",
            ns=("backend",),
            src=("raw/backend/payments.md:11",),
        ),
    ]


@pytest.fixture
def sample_manifest(sample_nodes, sample_edges) -> Manifest:
    return Manifest(
        schema_version=1, chunk_count=1,
        node_count=len(sample_nodes), edge_count=len(sample_edges),
        run_id="abc123def456", facts_hash="deadbeef",
    )


@pytest.fixture
def graph_dir(tmp_path: Path, sample_nodes, sample_edges, sample_manifest) -> Path:
    from lorekeep.compile.writer import write_graph
    g = tmp_path / "graph"
    write_graph(g, sample_nodes, sample_edges, sample_manifest)
    return g


@pytest.fixture
def wiki_dir(tmp_path: Path) -> Path:
    return tmp_path / "wiki"


# ── Unit tests ─────────────────────────────────────────────────────────────


class TestSlug:
    def test_colon_to_hyphen(self):
        assert _slug("svc:payments-api") == "svc-payments-api"

    def test_slash_to_hyphen(self):
        assert _slug("team:backend/squad") == "team-backend-squad"

    def test_no_special_chars(self):
        assert _slug("plain-id") == "plain-id"


class TestGenerateWiki:
    def test_generates_index(self, graph_dir, wiki_dir):
        result = generate_wiki(graph_dir, wiki_dir)
        assert (wiki_dir / "index.md").exists()
        assert result["nodes"] == 4
        assert result["edges"] == 2

    def test_generates_overview(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        overview = (wiki_dir / "overview.md").read_text()
        assert "# Graph Overview" in overview
        assert "Nodes" in overview
        assert "svc:payments-api" not in overview  # no raw IDs in stats
        assert "backend" in overview  # ns listed

    def test_entity_pages_created(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        assert (wiki_dir / "svc-payments-api.md").exists()
        assert (wiki_dir / "svc-auth.md").exists()
        assert (wiki_dir / "team-backend.md").exists()
        assert (wiki_dir / "dec-adr-007.md").exists()

    def test_entity_frontmatter(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert page.startswith("---")
        assert 'id: "svc:payments-api"' in page
        assert 'type: "service"' in page
        assert 'ns: ["backend"]' in page
        assert 'valid_from: "2024-01-15"' in page
        assert "sources:" in page
        assert "raw/backend/payments.md:3" in page
        assert "tags:" in page

    def test_entity_title_from_props(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "# payments-api" in page

    def test_entity_props_table(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "## Properties" in page
        assert "| name | payments-api |" in page
        assert "| lang | go |" in page

    def test_entity_outgoing_relationships(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "## Relationships" in page
        assert "depends_on" in page
        assert "[[svc-auth]]" in page
        assert "2024-01-15" in page

    def test_entity_incoming_relationships(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-auth.md").read_text()
        assert "[[svc-payments-api]]" in page

    def test_entity_timeline(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "## Timeline" in page
        assert "Valid from" in page

    def test_index_groups_by_type(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        index = (wiki_dir / "index.md").read_text()
        assert "## Services" in index
        assert "## Teams" in index
        assert "## Decisions" in index
        assert "[[svc-payments-api]]" in index
        assert "[[team-backend]]" in index

    def test_log_appended(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        generate_wiki(graph_dir, wiki_dir)
        log = (wiki_dir / "log.md").read_text()
        assert log.count("## [") == 2

    def test_log_format(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        log = (wiki_dir / "log.md").read_text()
        assert "# Lorekeep Wiki" in log
        assert "run_id=abc123def456" in log
        assert "4 nodes, 2 edges" in log

    def test_overview_shows_manifest_info(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        overview = (wiki_dir / "overview.md").read_text()
        assert "abc123def456" in overview
        assert "deadbeef" in overview

    def test_no_facts_returns_error(self, tmp_path, wiki_dir):
        result = generate_wiki(tmp_path / "empty", wiki_dir)
        assert "error" in result

    def test_wiki_without_manifest(self, graph_dir, wiki_dir):
        (graph_dir / "manifest.json").unlink()
        result = generate_wiki(graph_dir, wiki_dir)
        assert result["nodes"] == 4
        overview = (wiki_dir / "overview.md").read_text()
        assert "# Graph Overview" in overview

    def test_regenerates_clean(self, graph_dir, wiki_dir):
        """Re-generation wipes old pages (stale entities removed)."""
        generate_wiki(graph_dir, wiki_dir)
        stale = wiki_dir / "old-service.md"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("# stale")

        generate_wiki(graph_dir, wiki_dir)
        assert not stale.exists()
        assert (wiki_dir / "svc-payments-api.md").exists()


class TestDeterminism:
    def test_entity_pages_byte_identical(self, graph_dir, wiki_dir, tmp_path):
        """Re-generating unchanged facts yields identical pages (except log)."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        generate_wiki(graph_dir, dir_a)
        generate_wiki(graph_dir, dir_b)

        for f in ("index.md", "overview.md"):
            assert (dir_a / f).read_text() == (dir_b / f).read_text()

        for ent in dir_a.glob("*.md"):
            if ent.name in ("index.md", "overview.md", "log.md"):
                continue
            rel = ent.relative_to(dir_a)
            assert (dir_b / rel).read_text() == ent.read_text(), f"diff in {rel}"

    def test_sorted_output(self, graph_dir, wiki_dir):
        """Entity pages and index entries are sorted for deterministic output."""
        generate_wiki(graph_dir, wiki_dir)
        index = (wiki_dir / "index.md").read_text()
        svc_pos = index.find("[[svc-auth]]")
        svc2_pos = index.find("[[svc-payments-api]]")
        assert svc_pos < svc2_pos  # auth before payments-api (alphabetical)


# ── CLI tests ──────────────────────────────────────────────────────────────


class TestWikiCLI:
    def test_wiki_command(self, graph_dir, wiki_dir, monkeypatch):
        monkeypatch.setenv("LOREKEEP_OUT", str(graph_dir))
        monkeypatch.setenv("LOREKEEP_WIKI", str(wiki_dir))
        from lorekeep.cli import app
        result = runner.invoke(app, ["wiki"])
        assert result.exit_code == 0, result.stdout
        assert "pages written" in result.stdout
        assert (wiki_dir / "index.md").exists()

    def test_wiki_no_facts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "empty"))
        monkeypatch.setenv("LOREKEEP_WIKI", str(tmp_path / "wiki"))
        from lorekeep.cli import app
        result = runner.invoke(app, ["wiki"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_compile_auto_generates_wiki(self, tmp_path, monkeypatch, patch_make_provider):
        """compile should auto-generate wiki after successful compile."""
        from lorekeep.cli import app

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "backend").mkdir(parents=True)
        (home / "raw" / "backend" / "payments.md").write_text(
            "# Payments API\n\n## Service\n"
            "payments-api (go) depends on auth.\n"
            "team: backend owns payments-api.\n"
        )
        (home / "schema.json").write_text(
            json.dumps({
                "version": 1,
                "node_types": {
                    "service": {"props": {"name": "string", "lang": "string"}},
                    "team": {"props": {"name": "string"}},
                },
                "edge_types": {
                    "depends_on": {"from": "service", "to": "service"},
                    "owns": {"from": "team", "to": "service"},
                },
            })
        )

        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        result = runner.invoke(app, ["compile"])
        assert result.exit_code == 0, result.stdout

        assert (home / "wiki" / "index.md").exists()
        assert (home / "wiki" / "overview.md").exists()

    def test_resolve_regenerates_wiki(self, tmp_path, monkeypatch, patch_make_provider):
        """resolve should regenerate wiki after merging agent-proposed facts."""
        from lorekeep.cli import app
        from lorekeep.journal import append_journal
        from lorekeep.models import JournalEntry

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "backend").mkdir(parents=True)
        (home / "raw" / "backend" / "payments.md").write_text(
            "# Payments API\n\npayments-api (go) depends on auth.\n"
        )
        (home / "graph").mkdir(parents=True)
        (home / "pending").mkdir(parents=True)
        (home / "schema.json").write_text(
            json.dumps({
                "version": 1,
                "node_types": {
                    "service": {"props": {"name": "string", "lang": "string"}},
                },
                "edge_types": {
                    "depends_on": {"from": "service", "to": "service"},
                },
            })
        )

        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        runner.invoke(app, ["compile"])
        wiki_before = (home / "wiki" / "log.md").read_text()
        assert "## [" in wiki_before

        append_journal(
            home / "pending",
            JournalEntry(
                fact={"kind": "node", "id": "svc:new-svc", "type": "service",
                      "ns": ["backend"], "props": {"name": "new-svc"}},
                agent="test", ns="backend", confidence=0.9,
                proposed_at="2026-06-29T00:00:00Z",
            ),
            "backend",
        )

        result = runner.invoke(app, ["resolve"])
        assert result.exit_code == 0, result.stdout

        entity = home / "wiki" / "svc-new-svc.md"
        assert entity.exists()
        assert "# new-svc" in entity.read_text()

        wiki_after = (home / "wiki" / "log.md").read_text()
        assert wiki_after.count("## [") == wiki_before.count("## [") + 1


# ── Review-requested tests ─────────────────────────────────────────────────


class TestSyncInvariant:
    """Every node → entity page, every edge → wikilink on both endpoints."""

    def test_every_node_has_entity_page(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        from lorekeep.facts_io import read_facts
        from lorekeep.models import Node as NodeT
        for line in (graph_dir / "facts.jsonl").read_text().splitlines():
            d = json.loads(line)
            if d["kind"] != "node":
                continue
            slug = _slug(d["id"])
            page = wiki_dir / f"{slug}.md"
            assert page.exists(), f"missing entity page for {d['id']}"

    def test_every_edge_has_wikilinks(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        for line in (graph_dir / "facts.jsonl").read_text().splitlines():
            d = json.loads(line)
            if d["kind"] != "edge":
                continue
            from_slug = _slug(d["from"])
            to_slug = _slug(d["to"])
            from_page = wiki_dir.glob(f"{from_slug}.md")
            to_page = wiki_dir.glob(f"{to_slug}.md")
            from_pg = next(from_page, None)
            to_pg = next(to_page, None)
            assert from_pg, f"source page {from_slug} missing"
            assert to_pg, f"target page {to_slug} missing"
            assert f"[[{to_slug}]]" in from_pg.read_text(), \
                f"edge {d['id']}: [[{to_slug}]] missing on source page"
            assert f"[[{from_slug}]]" in to_pg.read_text(), \
                f"edge {d['id']}: [[{from_slug}]] missing on target page"


class TestYAMLFrontmatter:
    def test_frontmatter_parses_as_yaml(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        import yaml
        page = (wiki_dir / "svc-payments-api.md").read_text()
        fm_block = page.split("---")[1]
        data = yaml.safe_load(fm_block)
        assert data["id"] == "svc:payments-api"
        assert data["type"] == "service"
        assert data["ns"] == ["backend"]
        assert data["valid_from"] == "2024-01-15"
        assert data["sources"] == ["raw/backend/payments.md:3"]
        assert "entity" in data["tags"]

    def test_frontmatter_null_dates(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        import yaml
        page = (wiki_dir / "svc-auth.md").read_text()
        fm_block = page.split("---")[1]
        data = yaml.safe_load(fm_block)
        assert data["valid_from"] == ""

    def test_frontmatter_relationship_fields(self, graph_dir, wiki_dir):
        """Out-edges are emitted as frontmatter fields holding [[wikilinks]] —
        Tolaria detects these as relationships; Obsidian/Dataview query them."""
        generate_wiki(graph_dir, wiki_dir)
        import yaml
        page = (wiki_dir / "svc-payments-api.md").read_text()
        fm_block = page.split("---")[1]
        data = yaml.safe_load(fm_block)
        assert data["depends_on"] == ["[[svc-auth]]"]


class TestFlatLayout:
    """Unified flat layout: one <slug>.md per node at the wiki root (no
    entities/ subdir) — required for Tolaria's flat vault, fine for Obsidian."""

    def test_no_entities_subdir(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        assert not (wiki_dir / "entities").exists()

    def test_pages_at_root(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        for slug in ("svc-payments-api", "svc-auth", "team-backend", "dec-adr-007"):
            assert (wiki_dir / f"{slug}.md").exists(), slug


class TestEmptyGraph:
    def test_empty_graph_no_crash(self, tmp_path):
        graph = tmp_path / "graph"
        graph.mkdir()
        (graph / "facts.jsonl").write_text("")
        wiki = tmp_path / "wiki"
        result = generate_wiki(graph, wiki)
        assert result["nodes"] == 0
        assert result["edges"] == 0
        assert (wiki / "index.md").exists()
        assert (wiki / "overview.md").exists()
        assert (wiki / "log.md").exists()


class TestPropsSpecialChars:
    def test_pipe_in_prop_value(self, tmp_path):
        node = Node(
            id="svc:test", type="service", ns=("test",),
            props={"filter": "a | b", "name": "test"},
        )
        graph = tmp_path / "graph"
        from lorekeep.compile.writer import write_graph
        write_graph(graph, [node], [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        generate_wiki(graph, wiki)
        page = (wiki / "svc-test.md").read_text()
        assert "a \\| b" in page

    def test_newline_in_prop_value(self, tmp_path):
        node = Node(
            id="svc:multiline", type="service", ns=("test",),
            props={"desc": "line1\nline2", "name": "multiline"},
        )
        graph = tmp_path / "graph"
        from lorekeep.compile.writer import write_graph
        write_graph(graph, [node], [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        generate_wiki(graph, wiki)
        page = (wiki / "svc-multiline.md").read_text()
        assert "line1 line2" in page
        assert "\nline2 |" not in page

    def test_non_string_prop_value(self, tmp_path):
        node = Node(
            id="svc:typed", type="service", ns=("test",),
            props={"port": 8080, "enabled": True, "name": "typed"},
        )
        graph = tmp_path / "graph"
        from lorekeep.compile.writer import write_graph
        write_graph(graph, [node], [], Manifest(
            schema_version=1, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        generate_wiki(graph, wiki)
        page = (wiki / "svc-typed.md").read_text()
        assert "8080" in page
        assert "true" in page


class TestSlugCollision:
    def test_slug_collision_raises(self, tmp_path):
        nodes = [
            Node(id="svc:auth", type="service", ns=("t",), props={"name": "a"}),
            Node(id="svc/auth", type="service", ns=("t",), props={"name": "b"}),
        ]
        graph = tmp_path / "graph"
        from lorekeep.compile.writer import write_graph
        write_graph(graph, nodes, [], Manifest(
            schema_version=1, chunk_count=0, node_count=2, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        with pytest.raises(ValueError, match="slug collision"):
            generate_wiki(graph, wiki)


class TestWikiFailureSafe:
    def test_wiki_failure_does_not_crash_compile(self, tmp_path, monkeypatch, patch_make_provider):
        from lorekeep.cli import app

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "backend").mkdir(parents=True)
        (home / "raw" / "backend" / "payments.md").write_text(
            "# Payments API\n\npayments-api (go) depends on auth.\n"
        )
        (home / "schema.json").write_text(
            json.dumps({
                "version": 1,
                "node_types": {"service": {"props": {"name": "string", "lang": "string"}}},
                "edge_types": {"depends_on": {"from": "service", "to": "service"}},
            })
        )
        monkeypatch.setenv("LOREKEEP_HOME", str(home))
        monkeypatch.setattr(
            "lorekeep.wiki.generate_wiki",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = runner.invoke(app, ["compile"])
        assert result.exit_code == 0, result.stdout
        assert (home / "graph" / "facts.jsonl").exists()


class TestLogIntegrity:
    def test_log_preserves_prior_entries_verbatim(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        log1 = (wiki_dir / "log.md").read_text()
        first_entry = log1.split("\n\n")[-1] if "## [" in log1 else ""

        generate_wiki(graph_dir, wiki_dir)
        log2 = (wiki_dir / "log.md").read_text()

        assert log1 in log2
        assert log2.count("## [") == 2


class TestCompileSingleRegen:
    def test_compile_with_pending_single_log_entry(self, tmp_path, monkeypatch, patch_make_provider):
        """compile + pending resolve should produce exactly one new log entry."""
        from lorekeep.cli import app
        from lorekeep.journal import append_journal
        from lorekeep.models import JournalEntry

        home = tmp_path / "home"
        home.mkdir()
        (home / "raw" / "backend").mkdir(parents=True)
        (home / "raw" / "backend" / "payments.md").write_text(
            "# Payments API\n\npayments-api (go) depends on auth.\n"
        )
        (home / "pending" / "backend").mkdir(parents=True)
        (home / "schema.json").write_text(
            json.dumps({
                "version": 1,
                "node_types": {"service": {"props": {"name": "string", "lang": "string"}}},
                "edge_types": {"depends_on": {"from": "service", "to": "service"}},
            })
        )
        monkeypatch.setenv("LOREKEEP_HOME", str(home))

        append_journal(
            home / "pending",
            JournalEntry(
                fact={"kind": "node", "id": "svc:pre", "type": "service",
                      "ns": ["backend"], "props": {"name": "pre"}},
                agent="test", ns="backend", confidence=0.9,
                proposed_at="2026-06-29T00:00:00Z",
            ),
            "backend",
        )

        result = runner.invoke(app, ["compile"])
        assert result.exit_code == 0, result.stdout

        log = (home / "wiki" / "log.md").read_text()
        assert log.count("## [") == 1, f"expected 1 log entry, got {log.count('## [')}"

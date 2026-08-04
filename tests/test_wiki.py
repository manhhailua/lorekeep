"""Tests for wiki generation from facts.jsonl."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lorekeep.defaults import DEFAULT_SCHEMA
from lorekeep.models import ContentQuality, Edge, Manifest, Node, Schema
from lorekeep.wiki import _slug, generate_wiki

runner = CliRunner()


def _build_wiki(
    tmp_path: Path,
    nodes: list[Node],
    edges: list[Edge] | None = None,
) -> Path:
    """Write a small graph and return its generated wiki directory."""
    from lorekeep.compile.writer import write_graph

    edge_facts = edges or []
    graph = tmp_path / "graph"
    write_graph(graph, nodes, edge_facts, Manifest(
        schema_version=3,
        chunk_count=0,
        node_count=len(nodes),
        edge_count=len(edge_facts),
        run_id="test-run",
        facts_hash="test-hash",
    ))
    wiki = tmp_path / "wiki"
    generate_wiki(graph, wiki)
    return wiki


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
            props={"reason": "internal auth"},
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
        assert result["pages"] == 8  # 4 entity pages + index + catalog + overview + log
        assert (wiki_dir / "catalog.md").exists()

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
        assert 'kind: "node"' in page
        assert 'id: "svc:payments-api"' in page
        assert 'type: "service"' in page
        assert 'ns: ["backend"]' in page
        assert 'valid_from: "2024-01-15"' in page
        assert "sources:" in page
        assert "raw/backend/payments.md:3" in page
        assert "tags:" in page
        assert 'aliases: ["payments-api"]' in page

    def test_entity_title_from_props(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "# payments-api" in page

    def test_entity_at_a_glance(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "## At a glance" in page
        assert "- **Type:** Service" in page
        assert "- **Lang:** go" in page
        assert "| Key | Value |" not in page

    def test_entity_outgoing_relationships(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "## Connections" in page
        assert "### Depends on" in page
        assert "[[svc-auth|auth]]" in page
        assert "internal auth" in page
        assert "2024-01-15" in page

    def test_entity_incoming_relationships(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-auth.md").read_text()
        assert "[[svc-payments-api|payments-api]]" in page

    def test_relationship_rows_preserve_edge_fact_metadata(self, graph_dir, wiki_dir):
        """A human can audit a rendered relationship back to its edge fact."""
        generate_wiki(graph_dir, wiki_dir)
        source_page = (wiki_dir / "svc-payments-api.md").read_text()
        target_page = (wiki_dir / "svc-auth.md").read_text()
        assert "- [[svc-auth|auth]] — internal auth" in source_page
        assert "- [[svc-payments-api|payments-api]] — internal auth" in target_page
        for page in (source_page, target_page):
            assert "### Relationship facts" in page
            assert "`e_dep_1`" in page
            assert '["backend"]' in page
            assert "raw/backend/payments.md:6" in page
            assert "internal auth" in page

    def test_title_property_is_used_for_ontology_title_nodes(self, tmp_path):
        """goal/decision/document use title, not name, in ontology v2."""
        nodes = [
            Node(id="goal:ship", type="goal", ns=("team",), props={"title": "Ship v2"}),
            Node(id="dec:adr-1", type="decision", ns=("team",), props={"title": "Adopt v2"}),
            Node(id="doc:brief", type="document", ns=("team",), props={"title": "Design brief"}),
        ]
        graph = tmp_path / "graph"
        from lorekeep.compile.writer import write_graph
        write_graph(graph, nodes, [], Manifest(
            schema_version=3, chunk_count=0, node_count=3, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        generate_wiki(graph, wiki)

        assert "# Ship v2" in (wiki / "goal-ship.md").read_text()
        assert "# Adopt v2" in (wiki / "dec-adr-1.md").read_text()
        assert "# Design brief" in (wiki / "doc-brief.md").read_text()
        catalog = (wiki / "catalog.md").read_text()
        assert "[[goal-ship|Ship v2]]" in catalog
        assert "[[dec-adr-1|Adopt v2]]" in catalog
        assert "[[doc-brief|Design brief]]" in catalog
        import yaml
        frontmatter = yaml.safe_load(
            (wiki / "goal-ship.md").read_text().split("---")[1]
        )
        assert frontmatter["title"] == "Ship v2"
        assert frontmatter["props"]["title"] == "Ship v2"
        assert frontmatter["aliases"] == ["Ship v2"]

    def test_name_precedes_title_and_id_is_the_fallback(self, tmp_path):
        nodes = [
            Node(
                id="doc:both", type="document", ns=("team",),
                props={"name": "Human name", "title": "Document title"},
            ),
            Node(id="domain:opaque", type="domain", ns=("team",), props={}),
        ]
        wiki = _build_wiki(tmp_path, nodes)

        import yaml
        both_page = (wiki / "doc-both.md").read_text()
        both_fm = yaml.safe_load(both_page.split("---")[1])
        assert "# Human name" in both_page
        assert both_fm["aliases"] == ["Human name", "Document title"]
        assert both_fm["name"] == "Human name"
        assert both_fm["title"] == "Document title"

        fallback_page = (wiki / "domain-opaque.md").read_text()
        fallback_fm = yaml.safe_load(fallback_page.split("---")[1])
        assert "# domain:opaque" in fallback_page
        assert "aliases" not in fallback_fm
        assert fallback_fm["props"] == {}

    def test_entity_timeline(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        page = (wiki_dir / "svc-payments-api.md").read_text()
        assert "## Timeline" in page
        assert "Valid from" in page

    def test_catalog_groups_by_type(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        catalog = (wiki_dir / "catalog.md").read_text()
        assert "## Services" in catalog
        assert "## Teams" in catalog
        assert "## Decisions" in catalog
        assert "[[svc-payments-api|payments-api]]" in catalog
        assert "[[team-backend|team-backend]]" in catalog

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

    def test_regeneration_keeps_vault_root_and_obsidian_settings(
        self, graph_dir, wiki_dir,
    ):
        """Obsidian's watcher and private vault settings survive regeneration."""
        generate_wiki(graph_dir, wiki_dir)
        vault_inode = wiki_dir.stat().st_ino
        settings = wiki_dir / ".obsidian" / "app.json"
        settings.parent.mkdir()
        settings.write_text('{"showInlineTitle": false}')

        generate_wiki(graph_dir, wiki_dir)

        assert wiki_dir.stat().st_ino == vault_inode
        assert settings.read_text() == '{"showInlineTitle": false}'
        assert (wiki_dir / "svc-payments-api.md").exists()

    def test_failed_build_leaves_existing_vault_untouched(
        self, graph_dir, wiki_dir, monkeypatch,
    ):
        generate_wiki(graph_dir, wiki_dir)
        old_index = (wiki_dir / "index.md").read_text()
        settings = wiki_dir / ".obsidian" / "app.json"
        settings.parent.mkdir()
        settings.write_text("{}")

        monkeypatch.setattr(
            "lorekeep.wiki._entity_page",
            lambda *args: (_ for _ in ()).throw(RuntimeError("render failed")),
        )

        with pytest.raises(RuntimeError, match="render failed"):
            generate_wiki(graph_dir, wiki_dir)

        assert (wiki_dir / "index.md").read_text() == old_index
        assert settings.read_text() == "{}"

    def test_publish_replace_failure_rolls_back_all_markdown(
        self, graph_dir, wiki_dir, monkeypatch,
    ):
        generate_wiki(graph_dir, wiki_dir)
        for path in wiki_dir.glob("*.md"):
            path.write_text(f"# original {path.name}\n")
        settings = wiki_dir / ".obsidian" / "app.json"
        settings.parent.mkdir()
        settings.write_text("{}")
        before = {path.name: path.read_bytes() for path in wiki_dir.glob("*.md")}

        build_dir = wiki_dir.parent / ".wiki-build.tmp"
        real_replace = os.replace
        publish_attempts = 0

        def flaky_replace(src, dst):
            nonlocal publish_attempts
            src_path = Path(src)
            dst_path = Path(dst)
            if src_path.parent == build_dir and dst_path.parent == wiki_dir:
                publish_attempts += 1
                if publish_attempts == 2:
                    raise OSError("replace failed during publish")
            return real_replace(src, dst)

        monkeypatch.setattr("lorekeep.wiki.os.replace", flaky_replace)

        with pytest.raises(OSError, match="replace failed during publish"):
            generate_wiki(graph_dir, wiki_dir)

        after = {path.name: path.read_bytes() for path in wiki_dir.glob("*.md")}
        assert after == before
        assert settings.read_text() == "{}"
        assert not build_dir.exists()
        assert not (wiki_dir.parent / ".wiki-rollback.tmp").exists()

    def test_publish_unlink_failure_rolls_back_all_markdown(
        self, graph_dir, wiki_dir, monkeypatch,
    ):
        generate_wiki(graph_dir, wiki_dir)
        for path in wiki_dir.glob("*.md"):
            path.write_text(f"# original {path.name}\n")
        stale_names = {"a-stale.md", "b-stale.md"}
        for name in stale_names:
            (wiki_dir / name).write_text(f"# {name}\n")
        before = {path.name: path.read_bytes() for path in wiki_dir.glob("*.md")}

        real_unlink = Path.unlink
        stale_attempts = 0

        def flaky_unlink(path, *args, **kwargs):
            nonlocal stale_attempts
            if path.parent == wiki_dir and path.name in stale_names:
                stale_attempts += 1
                if stale_attempts == 2:
                    raise OSError("unlink failed during publish")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        with pytest.raises(OSError, match="unlink failed during publish"):
            generate_wiki(graph_dir, wiki_dir)

        after = {path.name: path.read_bytes() for path in wiki_dir.glob("*.md")}
        assert after == before
        assert not (wiki_dir.parent / ".wiki-build.tmp").exists()
        assert not (wiki_dir.parent / ".wiki-rollback.tmp").exists()

    def test_rollback_failure_preserves_recovery_snapshot(
        self, graph_dir, wiki_dir, monkeypatch,
    ):
        generate_wiki(graph_dir, wiki_dir)
        for path in wiki_dir.glob("*.md"):
            path.write_text(f"# original {path.name}\n")
        before = {path.name: path.read_bytes() for path in wiki_dir.glob("*.md")}

        build_dir = wiki_dir.parent / ".wiki-build.tmp"
        backup_dir = wiki_dir.parent / ".wiki-rollback.tmp"
        real_replace = os.replace
        publish_attempts = 0

        def fail_publish_and_restore(src, dst):
            nonlocal publish_attempts
            src_path = Path(src)
            dst_path = Path(dst)
            if src_path.parent == build_dir and dst_path.parent == wiki_dir:
                publish_attempts += 1
                if publish_attempts == 2:
                    raise OSError("publish failed")
            if (
                src_path.parent == wiki_dir
                and src_path.name.startswith(".lorekeep-rollback-")
                and dst_path.parent == wiki_dir
            ):
                raise OSError("rollback failed")
            return real_replace(src, dst)

        monkeypatch.setattr("lorekeep.wiki.os.replace", fail_publish_and_restore)

        with pytest.raises(RuntimeError, match="snapshot is preserved"):
            generate_wiki(graph_dir, wiki_dir)

        snapshot = {
            path.name: path.read_bytes() for path in backup_dir.glob("*.md")
        }
        assert snapshot == before
        assert not build_dir.exists()


class TestRichEntityProjection:
    def test_short_legacy_description_is_not_repeated_as_about(self, tmp_path):
        node = Node(
            id="svc:legacy", type="service", ns=("team",),
            props={"name": "Legacy", "description": "One-line service description."},
        )
        wiki = _build_wiki(tmp_path, [node])
        body = (wiki / "svc-legacy.md").read_text().split("---", 2)[2]

        assert body.count("One-line service description.") == 1
        assert "## About" not in body

    def test_description_is_readable_and_preserves_paragraphs(self, tmp_path):
        description = (
            "Tóm tắt miền AI.\n\n"
            "Giữ **Markdown**, Unicode và dấu | trong nội dung."
        )
        node = Node(
            id="domain:ai",
            type="domain",
            ns=("private",),
            props={"name": "AI", "description": description},
        )
        wiki = _build_wiki(tmp_path, [node])
        page = (wiki / "domain-ai.md").read_text()

        assert f"## About\n\n{description}" in page
        assert "| description |" not in page

        import yaml
        frontmatter = yaml.safe_load(page.split("---")[1])
        assert frontmatter["name"] == "AI"
        assert frontmatter["description"] == description
        assert frontmatter["props"] == node.props

        index = (wiki / "catalog.md").read_text()
        assert (
            "[[domain-ai|AI]] — Tóm tắt miền AI. "
            "Giữ **Markdown**, Unicode và dấu | trong nội dung."
        ) in index

    def test_empty_description_has_no_section_but_remains_lossless(self, tmp_path):
        node = Node(
            id="value:empty",
            type="value",
            ns=("private",),
            props={"name": "Empty description", "description": " \n "},
        )
        wiki = _build_wiki(tmp_path, [node])
        page = (wiki / "value-empty.md").read_text()

        assert "## Description" not in page
        assert "| description |" not in page

        import yaml
        frontmatter = yaml.safe_load(page.split("---")[1])
        assert frontmatter["description"] == " \n "
        assert frontmatter["props"] == node.props

    def test_typed_props_round_trip_and_safe_keys_are_queryable(self, tmp_path):
        props = {
            "name": "typed",
            "enabled": True,
            "port": 8080,
            "ratio": 0.5,
            "owners": ["Mạnh", "backend"],
            "config": {"z": 1, "a": 2},
            "none": None,
            "yes": "quoted YAML key",
            "needs:quote": "safe",
        }
        node = Node(
            id="svc:typed", type="service", ns=("test",), props=props,
        )
        wiki = _build_wiki(tmp_path, [node])
        page = (wiki / "svc-typed.md").read_text()

        import yaml
        frontmatter = yaml.safe_load(page.split("---")[1])
        assert frontmatter["props"] == props
        for key, value in props.items():
            assert frontmatter[key] == value
        assert 'config: {"a": 2, "z": 1}' in page

    def test_reserved_props_and_relationship_fields_do_not_overwrite_metadata(
        self, tmp_path,
    ):
        props = {
            "title": "Collision document",
            "kind": "runbook",
            "id": "shadow-id",
            "type": "shadow-type",
            "ns": ["shadow-ns"],
            "valid_from": "1999-01-01",
            "valid_to": "2000-01-01",
            "sources": ["shadow.md:1"],
            "tags": ["shadow"],
            "aliases": ["Shadow alias"],
            "props": {"nested": True},
            "relation_kind": "occupied",
        }
        source = Node(
            id="doc:collision",
            type="document",
            ns=("team",),
            props=props,
            src=("team/doc.md:1",),
        )
        target = Node(
            id="domain:target",
            type="domain",
            ns=("team",),
            props={"name": "Target"},
        )
        edges = [
            Edge(
                id="e-kind", type="kind",
                from_=source.id, to=target.id, ns=("team",),
            ),
            Edge(
                id="e-title", type="title",
                from_=source.id, to=target.id, ns=("team",),
            ),
        ]
        wiki = _build_wiki(tmp_path, [source, target], edges)

        import yaml
        page = (wiki / "doc-collision.md").read_text()
        frontmatter = yaml.safe_load(page.split("---")[1])
        assert frontmatter["kind"] == "node"
        assert frontmatter["id"] == source.id
        assert frontmatter["type"] == "document"
        assert frontmatter["ns"] == ["team"]
        assert frontmatter["valid_from"] == ""
        assert frontmatter["valid_to"] == ""
        assert frontmatter["sources"] == ["team/doc.md:1"]
        assert frontmatter["tags"] == ["document", "team", "entity"]
        assert frontmatter["aliases"] == ["Collision document"]
        assert frontmatter["props"] == props
        assert frontmatter["relation_kind"] == "occupied"
        assert frontmatter["relation_kind_2"] == ["[[domain-target]]"]
        assert frontmatter["relation_title"] == ["[[domain-target]]"]


class TestRichRelationships:
    def test_title_labels_and_parallel_edge_facts_are_preserved(self, tmp_path):
        source = Node(
            id="svc:source", type="service", ns=("team",),
            props={"name": "Source service"},
        )
        target = Node(
            id="dec:target", type="decision", ns=("team",),
            props={"title": "Target decision"},
        )
        edges = [
            Edge(
                id="edge-a",
                type="relates_to",
                from_=source.id,
                to=target.id,
                ns=("team",),
                valid_from=date(2025, 1, 1),
                props={"reason": "first"},
                src=("team/a.md:1",),
            ),
            Edge(
                id="edge-b",
                type="relates_to",
                from_=source.id,
                to=target.id,
                ns=("team",),
                valid_from=date(2026, 1, 1),
                props={"reason": "second"},
                src=("team/b.md:2",),
            ),
        ]
        wiki = _build_wiki(tmp_path, [source, target], edges)
        source_page = (wiki / "svc-source.md").read_text()
        target_page = (wiki / "dec-target.md").read_text()

        assert "[[dec-target|Target decision]] — first" in source_page
        assert "[[svc-source|Source service]] — first" in target_page
        for edge in edges:
            marker = f"`{edge.id}`"
            assert source_page.count(marker) == 1
            assert target_page.count(marker) == 1
            assert edge.src[0] in source_page
            assert edge.src[0] in target_page
            assert edge.props["reason"] in source_page
            assert edge.props["reason"] in target_page

        import yaml
        frontmatter = yaml.safe_load(source_page.split("---")[1])
        assert frontmatter["relates_to"] == ["[[dec-target]]"]


class TestHumanReadableProjection:
    def test_entity_page_is_human_first_and_uses_schema_relation_labels(self, tmp_path):
        from lorekeep.compile.writer import write_graph

        source = Node(
            id="svc:payments", type="service", ns=("team",),
            props={
                "name": "Payments",
                "summary": "Xử lý yêu cầu thanh toán của khách hàng.",
                "description": "Dịch vụ lõi của luồng checkout.",
                "status": "active",
                "lang": "Go",
            },
            src=("team/payments.md:1",),
        )
        target = Node(
            id="svc:auth", type="service", ns=("team",),
            props={"name": "Auth", "summary": "Xác thực danh tính dịch vụ."},
        )
        edge = Edge(
            id="e_depends_on_0001", type="depends_on",
            from_=source.id, to=target.id, ns=("team",),
            props={"description": "Dùng để xác thực token trước khi thu tiền."},
            src=("team/payments.md:4",),
        )
        graph = tmp_path / "graph"
        write_graph(graph, [source, target], [edge], Manifest(
            schema_version=4, chunk_count=1, node_count=2, edge_count=1,
            run_id="human", facts_hash="hash",
        ))
        schema = Schema.load(DEFAULT_SCHEMA)
        wiki = tmp_path / "wiki"

        generate_wiki(graph, wiki, schema=schema)

        page = (wiki / "svc-payments.md").read_text()
        assert page.index("# Payments") < page.index("> Xử lý yêu cầu")
        assert page.index("> Xử lý yêu cầu") < page.index("## About")
        assert page.index("## About") < page.index("## At a glance")
        assert page.index("## At a glance") < page.index("## Connections")
        assert page.index("## Connections") < page.index("## Sources")
        assert page.index("## Sources") < page.index("## Technical details")
        assert "### Depends on" in page
        assert (
            "- [[svc-auth|Auth]] — Dùng để xác thực token trước khi thu tiền."
            in page
        )
        assert "| Entity | Label | Fact ID |" not in page

        inverse = (wiki / "svc-auth.md").read_text()
        assert "### Depended on by" in inverse

    def test_landing_catalog_and_stale_schema_warning(self, tmp_path):
        from lorekeep.compile.writer import write_graph

        nodes = [
            Node(
                id="goal:ship", type="goal", ns=("me",),
                props={"title": "Ship ontology v2", "summary": "Hoàn thiện ontology.", "status": "active"},
            ),
            Node(
                id="person:manh", type="person", ns=("me",),
                props={"name": "Mạnh", "summary": "Người duy trì Lorekeep."},
            ),
        ]
        graph = tmp_path / "graph"
        write_graph(graph, nodes, [], Manifest(
            schema_version=3, chunk_count=1, node_count=2, edge_count=0,
            run_id="stale", facts_hash="hash",
            content_quality=ContentQuality(
                node_label_coverage=1.0,
                node_summary_coverage=1.0,
                node_description_coverage=0.0,
                edge_description_coverage=1.0,
            ),
        ))
        wiki = tmp_path / "wiki"

        generate_wiki(graph, wiki, schema=Schema.load(DEFAULT_SCHEMA))

        index = (wiki / "index.md").read_text()
        catalog = (wiki / "catalog.md").read_text()
        assert "## Goals and projects" in index
        assert "## People and teams" in index
        assert "Graph schema is out of date" in index
        assert "schema v3" in index and "schema is v4" in index
        assert "## Goals" in catalog
        assert "## People" in catalog
        assert "[[person-manh|Mạnh]] — Người duy trì Lorekeep." in catalog

    def test_legacy_fact_gets_truthful_structural_fallback(self, tmp_path):
        node = Node(id="domain:legacy", type="domain", ns=("team",), props={})
        wiki = _build_wiki(tmp_path, [node])

        page = (wiki / "domain-legacy.md").read_text()

        assert "> domain:legacy — Domain." in page
        assert "## At a glance" in page


class TestDeterminism:
    def test_entity_pages_byte_identical(self, graph_dir, wiki_dir, tmp_path):
        """Re-generating unchanged facts yields identical pages (except log)."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        generate_wiki(graph_dir, dir_a)
        generate_wiki(graph_dir, dir_b)

        for f in ("index.md", "catalog.md", "overview.md"):
            assert (dir_a / f).read_text() == (dir_b / f).read_text()

        for ent in dir_a.glob("*.md"):
            if ent.name in ("index.md", "catalog.md", "overview.md", "log.md"):
                continue
            rel = ent.relative_to(dir_a)
            assert (dir_b / rel).read_text() == ent.read_text(), f"diff in {rel}"

    def test_sorted_output(self, graph_dir, wiki_dir):
        """Entity pages and index entries are sorted for deterministic output."""
        generate_wiki(graph_dir, wiki_dir)
        index = (wiki_dir / "catalog.md").read_text()
        svc_pos = index.find("[[svc-auth|auth]]")
        svc2_pos = index.find("[[svc-payments-api|payments-api]]")
        assert svc_pos < svc2_pos  # auth before payments-api (alphabetical)

    def test_nested_props_have_stable_key_order(self, tmp_path):
        node = Node(
            id="svc:nested",
            type="service",
            ns=("test",),
            props={
                "name": "nested",
                "config": {"z": 1, "a": {"last": 2, "first": 1}},
            },
        )
        wiki = _build_wiki(tmp_path, [node])
        page = (wiki / "svc-nested.md").read_text()

        expected = '{"a": {"first": 1, "last": 2}, "z": 1}'
        assert f"  config: {expected}" in page
        assert f"config: {expected}" in page


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
    """Every fact has a corresponding, auditable wiki projection."""

    def test_every_node_has_entity_page(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        for line in (graph_dir / "facts.jsonl").read_text().splitlines():
            d = json.loads(line)
            if d["kind"] != "node":
                continue
            slug = _slug(d["id"])
            page = wiki_dir / f"{slug}.md"
            assert page.exists(), f"missing entity page for {d['id']}"

    def test_every_node_frontmatter_matches_its_fact(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        import yaml

        for line in (graph_dir / "facts.jsonl").read_text().splitlines():
            fact = json.loads(line)
            if fact["kind"] != "node":
                continue
            page = (wiki_dir / f"{_slug(fact['id'])}.md").read_text()
            frontmatter = yaml.safe_load(page.split("---")[1])
            assert frontmatter["kind"] == fact["kind"]
            assert frontmatter["id"] == fact["id"]
            assert frontmatter["type"] == fact["type"]
            assert frontmatter["ns"] == fact["ns"]
            assert frontmatter["valid_from"] == (fact["valid_from"] or "")
            assert frontmatter["valid_to"] == (fact["valid_to"] or "")
            assert frontmatter["sources"] == fact["src"]
            assert frontmatter["props"] == fact["props"]

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
            assert f"[[{to_slug}" in from_pg.read_text(), \
                f"edge {d['id']}: [[{to_slug}...]] missing on source page"
            assert f"[[{from_slug}" in to_pg.read_text(), \
                f"edge {d['id']}: [[{from_slug}...]] missing on target page"

    def test_every_edge_metadata_is_visible_on_both_endpoints(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        for line in (graph_dir / "facts.jsonl").read_text().splitlines():
            fact = json.loads(line)
            if fact["kind"] != "edge":
                continue
            pages = [
                wiki_dir / f"{_slug(fact['from'])}.md",
                wiki_dir / f"{_slug(fact['to'])}.md",
            ]
            for page in pages:
                text = page.read_text()
                assert f"`{fact['id']}`" in text
                assert json.dumps(fact["ns"]) in text
                for source in fact["src"]:
                    assert source in text
                for key, value in fact["props"].items():
                    assert key in text
                    assert str(value) in text


class TestYAMLFrontmatter:
    def test_frontmatter_parses_as_yaml(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        import yaml
        page = (wiki_dir / "svc-payments-api.md").read_text()
        fm_block = page.split("---")[1]
        data = yaml.safe_load(fm_block)
        assert data["kind"] == "node"
        assert data["id"] == "svc:payments-api"
        assert data["type"] == "service"
        assert data["ns"] == ["backend"]
        assert data["valid_from"] == "2024-01-15"
        assert data["sources"] == ["raw/backend/payments.md:3"]
        assert "entity" in data["tags"]
        assert data["props"] == {"name": "payments-api", "lang": "go"}
        assert data["name"] == "payments-api"
        assert data["lang"] == "go"

    def test_frontmatter_null_dates(self, graph_dir, wiki_dir):
        generate_wiki(graph_dir, wiki_dir)
        import yaml
        page = (wiki_dir / "svc-auth.md").read_text()
        fm_block = page.split("---")[1]
        data = yaml.safe_load(fm_block)
        assert data["valid_from"] == ""

    def test_frontmatter_preserves_unicode_for_obsidian_readability(self, tmp_path):
        node = Node(
            id="person:nguyễn", type="person", ns=("cá-nhân",),
            props={"name": "Mạnh"}, src=("cá-nhân/about.md:1",),
        )
        graph = tmp_path / "graph"
        from lorekeep.compile.writer import write_graph
        write_graph(graph, [node], [], Manifest(
            schema_version=3, chunk_count=0, node_count=1, edge_count=0,
            run_id="x", facts_hash="y",
        ))
        wiki = tmp_path / "wiki"
        generate_wiki(graph, wiki)
        page = (wiki / "person-nguyễn.md").read_text()
        assert 'id: "person:nguyễn"' in page
        assert 'ns: ["cá-nhân"]' in page
        assert 'aliases: ["Mạnh"]' in page
        assert 'name: "Mạnh"' in page

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
        assert (wiki / "catalog.md").exists()
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
        assert "a | b" in page

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


def test_slug_preserves_vietnamese_diacritics():
    """_slug must keep diacritics (only : / -> -); stripping loses meaning."""
    from lorekeep.wiki import _slug
    assert _slug("person:nguyễn") == "person-nguyễn"
    assert _slug("domain:ẩm-thực") == "domain-ẩm-thực"

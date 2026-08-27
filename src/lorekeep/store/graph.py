"""GraphStore: load facts.jsonl into a networkx MultiDiGraph with temporal queries.

Pure graph logic. No permission, no MCP. Permission is applied by perm.ScopedGraph.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import networkx as nx

from lorekeep.facts_io import read_facts
from lorekeep.models import Edge, Node


def parse_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    return date.fromisoformat(value)


def is_quarantined(node: Node) -> bool:
    """True if a node was parked for orphan review (``lorekeep quarantine``, #266).

    Quarantine is a props flag (``quarantined_at``/``quarantined_reason``), not a
    model field — it survives recompiles the same way ``merged_ids`` does, by
    being read back from the previous ``facts.jsonl`` and reapplied in resolve.
    """
    return bool(node.props.get("quarantined_at"))


class GraphStore:
    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self._G = nx.MultiDiGraph()
        self._alias_to_canonical: dict[str, str] = {}
        self._edges_by_id: dict[str, Edge] = {}
        for n in nodes:
            self._G.add_node(n.id, node=n)
            # Build reverse alias index from merged_ids props
            for mid in n.props.get("merged_ids", []):
                if isinstance(mid, str) and mid != n.id:
                    self._alias_to_canonical[mid] = n.id
        for e in edges:
            self._G.add_edge(e.from_, e.to, key=e.id, edge=e)
            self._edges_by_id[e.id] = e

    def resolve_alias(self, id: str) -> str:
        """Resolve an alias ID to its canonical ID.

        Returns the ID unchanged if it is not a known alias. This enables
        query-time alias resolution: ``get_node("person:manhhailua")``
        returns the canonical node (e.g. ``person:manhpt1``) that absorbed it.
        """
        return self._alias_to_canonical.get(id, id)

    @classmethod
    def from_jsonl(cls, path: Path) -> "GraphStore":
        facts = read_facts(path)
        nodes = [f for f in facts if isinstance(f, Node)]
        edges = [f for f in facts if isinstance(f, Edge)]
        return cls(nodes, edges)

    def node_ids(self) -> set[str]:
        return set(self._G.nodes)

    def get_node(self, id: str) -> Node | None:
        cid = self.resolve_alias(id)
        if cid not in self._G:
            return None
        return self._G.nodes[cid]["node"]

    def get_edge(self, id: str) -> Edge | None:
        return self._edges_by_id.get(id)

    def all_nodes(self) -> list[Node]:
        return [d["node"] for _, d in self._G.nodes(data=True) if "node" in d]

    def all_namespaces(self) -> set[str]:
        """Every namespace present in the graph (nodes + edges)."""
        ns: set[str] = set()
        for n in self.all_nodes():
            ns.update(n.ns)
        for e in self.all_edges():
            ns.update(e.ns)
        return ns

    def all_edges(self) -> list[Edge]:
        return [d["edge"] for _, _, d in self._G.edges(data=True, keys=False)]

    def out_edges(self, id: str, edge_type: str | None = None) -> list[Edge]:
        result = []
        for _, _, d in self._G.out_edges(id, data=True, keys=False):
            e = d["edge"]
            if edge_type is None or e.type == edge_type:
                result.append(e)
        return result

    def in_edges(self, id: str, edge_type: str | None = None) -> list[Edge]:
        result = []
        for _, _, d in self._G.in_edges(id, data=True, keys=False):
            e = d["edge"]
            if edge_type is None or e.type == edge_type:
                result.append(e)
        return result

    def neighbors(self, id: str, edge_type: str | None = None, depth: int = 1) -> dict:
        """BFS over both directions up to `depth`. Returns {nodes:[Node], edges:[Edge]}."""
        cid = self.resolve_alias(id)
        if cid not in self._G:
            return {"nodes": [], "edges": []}
        seen_nodes = {cid}
        seen_edges: set[str] = set()
        out_nodes: list[Node] = []
        out_edges: list[Edge] = []
        frontier = [cid]
        for _ in range(max(depth, 0)):
            nxt: list[str] = []
            for u in frontier:
                for e in self.out_edges(u, edge_type) + self.in_edges(u, edge_type):
                    if e.id in seen_edges:
                        continue
                    seen_edges.add(e.id)
                    out_edges.append(e)
                    other = e.to if e.from_ == u else e.from_
                    if other not in seen_nodes and other in self._G:
                        seen_nodes.add(other)
                        nxt.append(other)
                        out_nodes.append(self._G.nodes[other]["node"])
            frontier = nxt
        return {"nodes": [self.get_node(id)] + out_nodes, "edges": out_edges}

    def distances_from(self, center: str, cap: int = 4) -> dict[str, int]:
        """Undirected hop distances from ``center``, capped. Empty if unknown."""
        cid = self.resolve_alias(center)
        if cid not in self._G:
            return {}
        dist = {cid: 0}
        frontier = [cid]
        depth = 0
        while frontier and depth < cap:
            nxt: list[str] = []
            depth += 1
            for u in frontier:
                for e in self.out_edges(u) + self.in_edges(u):
                    other = e.to if e.from_ == u else e.from_
                    if other not in dist and other in self._G:
                        dist[other] = depth
                        nxt.append(other)
            frontier = nxt
        return dist

    @staticmethod
    def _active(valid_from: date | None, valid_to: date | None, t: date) -> bool:
        """Half-open [valid_from, valid_to): None means unbounded on that side."""
        if valid_from is not None and t < valid_from:
            return False
        if valid_to is not None and not (t < valid_to):
            return False
        return True

    def snapshot(self, time: date) -> tuple[list[Node], list[Edge]]:
        nodes = [n for n in self.all_nodes()
                 if self._active(n.valid_from, n.valid_to, time)]
        edges = [e for e in self.all_edges()
                 if self._active(e.valid_from, e.valid_to, time)]
        return nodes, edges

    def history(self, id: str) -> list[dict]:
        """Node + all edges touching it, ordered by valid_from (None first)."""
        cid = self.resolve_alias(id)
        node = self.get_node(cid)
        if node is None:
            return []
        touching = self.out_edges(cid) + self.in_edges(cid)
        touching.sort(key=lambda e: e.valid_from or date.min)
        items: list[dict] = [{"kind": "node", **node.model_dump(mode="json", by_alias=True)}]
        for e in touching:
            items.append({"kind": "edge", **e.model_dump(mode="json", by_alias=True)})
        return items

    def changes(self, from_t: date, to_t: date) -> dict:
        """Edges whose validity began or ended within [from_t, to_t)."""
        began: list[dict] = []
        ended: list[dict] = []
        for e in self.all_edges():
            ed = e.model_dump(mode="json", by_alias=True)
            if e.valid_from is not None and from_t <= e.valid_from < to_t:
                began.append(ed)
            if e.valid_to is not None and from_t <= e.valid_to < to_t:
                ended.append(ed)
        return {"began": began, "ended": ended}

    def search(self, query: str, limit: int = 10, fts=None) -> list[str]:
        """Return node ids matching query. Uses an FTSIndex if given, else scan."""
        if fts is not None:
            return fts.search_nodes(query, limit)
        from lorekeep.store.fts import scan_search
        return scan_search(self.all_nodes(), query, limit)

    def search_facts(self, query: str, limit: int = 10, fts=None) -> list[Edge]:
        """Return relationship facts matching query (edge type, endpoints, props)."""
        if fts is not None:
            ids = fts.search_edges(query, limit)
            return [edge for eid in ids if (edge := self.get_edge(eid)) is not None]
        from lorekeep.store.fts import endpoint_names, scan_search_edges
        return scan_search_edges(
            self.all_edges(), query, limit,
            endpoint_names=endpoint_names(self.all_nodes()),
        )

    def stats(self) -> dict:
        """Return graph statistics: counts by type/ns, provenance split, freshness."""
        nodes = self.all_nodes()
        edges = self.all_edges()
        node_types: dict[str, int] = {}
        edge_types: dict[str, int] = {}
        all_ns: set[str] = set()
        curator = 0
        agent = 0
        for n in nodes:
            node_types[n.type] = node_types.get(n.type, 0) + 1
            all_ns.update(n.ns)
            if n.src:
                curator += 1
            else:
                agent += 1
        for e in edges:
            edge_types[e.type] = edge_types.get(e.type, 0) + 1
            all_ns.update(e.ns)
        today = date.today()
        valid_tos = [n.valid_to for n in nodes if n.valid_to]
        valid_froms = [n.valid_from for n in nodes if n.valid_from]
        return {
            "nodes": len(nodes),
            "edges": len(edges),
            "node_types": dict(sorted(node_types.items())),
            "edge_types": dict(sorted(edge_types.items())),
            "namespaces": sorted(all_ns),
            "provenance": {"curator": curator, "agent": agent},
            "freshness": {
                "oldest": min(valid_froms).isoformat() if valid_froms else None,
                "newest": max(valid_froms).isoformat() if valid_froms else None,
                "expired": sum(1 for t in valid_tos if t <= today),
            },
        }

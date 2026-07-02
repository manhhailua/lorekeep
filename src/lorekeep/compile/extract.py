"""Extract: turn a DocChunk into candidate facts via an LLM. Pure helpers first."""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from lorekeep.models import DocChunk, Edge, Node, Schema

SYSTEM_PROMPT_BASE = (
    "You are a knowledge-graph extractor. Read the document chunk and emit a JSON "
    'object {"nodes":[...], "edges":[...], "aliases":{...}}. '
    "Only use node_types and edge_types listed in the provided schema. "
    "For every node give id (stable slug prefixed by type, e.g. svc:payments-api), "
    "type, name, optional props, optional valid_from/valid_to (ISO dates, null = unknown). "
    "For every edge give type, from (node id), to (node id), optional valid_from/valid_to. "
    "aliases maps a canonical name to surface variants. Emit NO text outside the JSON."
)


def build_system_prompt(schema: Schema) -> str:
    """Build a constant system prompt including schema — cacheable across chunks.

    Keeping schema in the system prompt (not user message) maximizes prefix
    cache hits: the system message is identical for every chunk in a compile run.
    """
    node_types = ", ".join(schema.node_types.keys())
    edge_types = ", ".join(
        f"{k}({v.from_}->{v.to})" for k, v in schema.edge_types.items()
    )
    return (
        f"{SYSTEM_PROMPT_BASE}\n\n"
        f"Allowed node_types: {node_types}\n"
        f"Allowed edge_types: {edge_types}"
    )


# Backward compat
SYSTEM_PROMPT = SYSTEM_PROMPT_BASE


def build_prompt(chunk: DocChunk, schema: Schema) -> str:
    """Build user message — just the chunk text (no schema/src/ns).

    Schema is in the system prompt (see build_system_prompt). src and ns
    are extracted from chunk by parse_response for provenance, not needed
    in the LLM prompt.
    """
    return chunk.text


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    return date.fromisoformat(v)


def _extract_json(raw: str, chunk: DocChunk) -> str:
    """Best-effort recover a JSON object from LLM output.

    response_format=json_object usually yields clean JSON, but some models wrap
    output in ```json fences or prepend prose. Strip fences, then fall back to
    the first balanced {...} span. Raises ValueError (with chunk src) if the
    output still can't be parsed, so the pipeline reports a clear failure.
    """
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        brace = s.find("{")
        if brace != -1:
            s = s[brace:]
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            json.loads(m.group(0))      # validate; raises if malformed
            return m.group(0)
        raise ValueError(f"LLM returned non-JSON for {chunk.src}")


def parse_response(
    raw: str, chunk: DocChunk, schema: Schema | None = None,
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    data = json.loads(_extract_json(raw, chunk))
    nodes: list[Node] = []
    for n in data.get("nodes", []):
        ntype = n.get("type")
        if schema is not None and not schema.is_valid_node_type(ntype):
            continue
        props = dict(n.get("props", {}))
        if "name" in n and "name" not in props:
            props["name"] = n["name"]
        nodes.append(Node(
            id=n["id"],
            type=ntype,
            ns=(chunk.namespace,),
            valid_from=_parse_date(n.get("valid_from")),
            valid_to=_parse_date(n.get("valid_to")),
            props=props,
            src=(chunk.src,),
        ))
    edges: list[Edge] = []
    for e in data.get("edges", []):
        etype = e.get("type")
        if schema is not None and not schema.is_valid_edge_type(etype):
            continue
        edges.append(Edge(
            id="",                      # assigned deterministically in resolve
            type=etype,
            **{"from": e["from"]},
            to=e["to"],
            ns=(chunk.namespace,),
            valid_from=_parse_date(e.get("valid_from")),
            valid_to=_parse_date(e.get("valid_to")),
            src=(chunk.src,),
        ))
    aliases = {k: list(v) for k, v in data.get("aliases", {}).items()}
    return nodes, edges, aliases


import hashlib
from pathlib import Path

from lorekeep.compile.providers import LLMProvider


class ExtractionCache:
    """Maps (chunk_hash, schema_version) -> raw LLM response. Local only."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def key(self, chunk: DocChunk, schema_version: int, model: str = "") -> str:
        h = hashlib.sha256()
        h.update(str(schema_version).encode("utf-8"))
        h.update(b"\n")
        h.update(model.encode("utf-8"))
        h.update(b"\n")
        h.update(chunk.hash.encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, raw: str) -> None:
        self._data[key] = raw

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, sort_keys=True, indent=2), encoding="utf-8"
        )


def extract_chunk(
    chunk: DocChunk, schema: Schema, provider: LLMProvider, cache: ExtractionCache,
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    model = getattr(provider, "model", "")
    key = cache.key(chunk, schema.version, model)
    raw = cache.get(key)
    if raw is None:
        system = build_system_prompt(schema)
        raw = provider.extract_json(system, chunk.text)
        cache.set(key, raw)
    return parse_response(raw, chunk, schema)

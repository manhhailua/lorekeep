"""Extract: turn a DocChunk into candidate facts via an LLM. Pure helpers first."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any

from lorekeep.models import DocChunk, Edge, Node, Schema

log = logging.getLogger("lorekeep")

SYSTEM_PROMPT_BASE = (
    "You are a knowledge-graph extractor. Read the document chunk and emit a JSON "
    'object {"nodes":[...], "edges":[...], "aliases":{...}}. '
    "Only use node_types and edge_types listed in the provided schema. "
    "For every node give id (stable slug prefixed by type, e.g. svc:payments-api), "
    "type, optional props using the preferred keys for that type, and optional "
    "valid_from/valid_to (ISO dates, null = unknown). "
    "For every edge give type, from (node id), to (node id), optional props, "
    "and optional valid_from/valid_to. "
    "aliases maps a canonical name to surface variants. Emit NO text outside the JSON."
)

HUMAN_READABLE_RULE = (
    "Human-readable content rule: every node must have a concise name or title "
    "and a one-sentence props.summary that tells a human what it is and why it "
    "matters here. Add props.description when the source provides enough detail. "
    "Every edge must have props.description explaining the concrete reason, "
    "mechanism, or context for that relationship. Ground all prose in the source; "
    "do not invent details or repeat generic templates. Write in the same language "
    "as the source chunk. Keep summary suitable for a one-line catalog and put "
    "longer or multi-paragraph material in description."
)

# Altitude rule: decides node vs attribute. Prevents low-altitude tokens
# (commands, env vars, filenames, errors) from becoming nodes — the root cause
# of the "concept noise" wiki reflection (192 nodes, 98 trash concepts).
ALTITUDE_RULE = (
    "Altitude rule: create a node ONLY for a stable semantic entity "
    "(person, service, project, decision, domain, skill, role, goal, team, "
    "document). Low-altitude tokens — commands, environment variables, "
    "filenames, error strings, tool names, metric names — must NOT become "
    "nodes; attach them as properties of the nearest relevant node. Prefer a "
    "specific semantic edge (depends_on, contributes_to, has_skill, decided_by) "
    "over a generic one (relates_to)."
)

TEMPORAL_RULE = (
    "Temporal rule: valid_from/valid_to belong to the exact fact whose lifetime "
    "the text describes. If a relationship starts or ends, set dates on that "
    "edge only. Do NOT copy an edge's valid_to to either endpoint node unless "
    "the text explicitly says the entity itself ceased to exist. Examples: "
    "'service launched on D' means that service node has valid_from D; "
    "'dependency removed on D' means that dependency edge has valid_to D while "
    "both endpoint service nodes remain open."
)

# Subject-centric extraction for the personal namespace: anchor on the person,
# capture role/skill/domain/goal/preference, link to team entities cross-ns.
SUBJECT_PROMPT = (
    "This chunk is in the 'me' namespace — the user's personal profile. Extract "
    "subject-centric facts: anchor on ONE canonical person node for the user and "
    "capture their role, skills, domains, goals, preferences, and values. Link the "
    "subject to any team project/service mentioned via cross-namespace edges "
    "(owns, contributes_to, works_on, has_skill, collaborates_with). Do NOT split "
    "the subject into multiple person nodes — emit a single canonical person id."
)


def _endpoint_label(value: str | tuple[str, ...]) -> str:
    return value if isinstance(value, str) else "|".join(value)


def build_system_prompt(
    schema: Schema,
    ns: str | None = None,
    personal_ns: str = "me",
) -> str:
    """Build a constant system prompt including schema + altitude rule.

    ns='me' adds subject-centric guidance (personal profile); other namespaces
    use the default entity-centric extraction. Keeping schema in the system
    prompt (not user message) maximizes prefix cache hits across chunks.
    """
    node_types = ", ".join(
        f"{name}("
        + ", ".join(
            f"{prop}:{kind}"
            for prop, kind in {**spec.props, **schema.common_node_props}.items()
        )
        + ")"
        for name, spec in schema.node_types.items()
    )
    edge_types = ", ".join(
        f"{k}({_endpoint_label(v.from_)}->{_endpoint_label(v.to)}; props: "
        + ", ".join(
            f"{prop}:{kind}"
            for prop, kind in {**v.props, **schema.common_edge_props}.items()
        )
        + ")"
        for k, v in schema.edge_types.items()
    )
    parts = [
        SYSTEM_PROMPT_BASE,
        HUMAN_READABLE_RULE,
        ALTITUDE_RULE,
        TEMPORAL_RULE,
        f"Allowed node_types and preferred props: {node_types}",
        f"Allowed edge_types: {edge_types}",
    ]
    if ns == personal_ns:
        parts.append(SUBJECT_PROMPT.replace("'me'", repr(personal_ns)))
    return "\n\n".join(parts)


# Backward compat: base prompt without altitude/ns additions.
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
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v)
    except ValueError:
        # Some providers return a full ISO-8601 timestamp even though the graph
        # contract stores day precision. Preserve the calendar date expressed
        # by the model; do not shift it through UTC before truncating.
        return datetime.fromisoformat(v).date()


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
            log.debug("dropping node with unknown type %r in %s", ntype, chunk.src)
            continue
        props = dict(n.get("props", {}))
        for key in ("name", "title", "summary", "description"):
            if key in n and key not in props:
                props[key] = n[key]
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
            log.debug("dropping edge with unknown type %r in %s", etype, chunk.src)
            continue
        props = dict(e.get("props", {}))
        if "description" in e and "description" not in props:
            props["description"] = e["description"]
        edges.append(Edge(
            id="",                      # assigned deterministically in resolve
            type=etype,
            **{"from": e["from"]},
            to=e["to"],
            ns=(chunk.namespace,),
            valid_from=_parse_date(e.get("valid_from")),
            valid_to=_parse_date(e.get("valid_to")),
            props=props,
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

    def key(
        self,
        chunk: DocChunk,
        schema_version: int,
        model: str = "",
        prompt_variant: str = "",
    ) -> str:
        h = hashlib.sha256()
        h.update(str(schema_version).encode("utf-8"))
        h.update(b"\n")
        h.update(model.encode("utf-8"))
        h.update(b"\n")
        h.update(prompt_variant.encode("utf-8"))
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
    chunk: DocChunk,
    schema: Schema,
    provider: LLMProvider,
    cache: ExtractionCache,
    personal_ns: str = "me",
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    model = getattr(provider, "model", "")
    system = build_system_prompt(schema, chunk.namespace, personal_ns)
    schema_json = json.dumps(
        schema.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        ensure_ascii=False,
    )
    schema_fingerprint = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
    prompt_fingerprint = hashlib.sha256(system.encode("utf-8")).hexdigest()
    prompt_variant = (
        f"schema={schema_fingerprint};prompt={prompt_fingerprint};"
        f"personal={personal_ns};"
        f"subject={chunk.namespace == personal_ns}"
    )
    key = cache.key(chunk, schema.version, model, prompt_variant)
    raw = cache.get(key)
    if raw is None:
        raw = provider.extract_json(system, chunk.text)
        cache.set(key, raw)
    return parse_response(raw, chunk, schema)

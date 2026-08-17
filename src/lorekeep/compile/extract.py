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
    "For every node give id (stable slug prefixed by type, e.g. svc:payments-api "
    "for a service, prj:checkout-redesign for a project, person:alice for a person). "
    "Use the id_prefix shown for each type in the schema — e.g. if a type has "
    "id_prefix 'svc', name it svc:my-service, NOT service:my-service. "
    "Give each node a type and props using the keys listed for that type — fill "
    "every key the source supports evidence for, not just the name and summary — "
    "plus optional valid_from/valid_to (ISO dates, null = unknown). "
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
    "do not invent details or repeat generic templates. Keep summary suitable for "
    "a one-line catalog and put "
    "longer or multi-paragraph material in description."
)

OUTPUT_LANGUAGE_RULE = (
    "Output language rule: write all generated human-readable content in "
    "the language identified by ISO 639-1 code '{language}', regardless of the "
    "source chunk's language. This includes node names or titles, summaries, "
    "descriptions, and edge descriptions. Preserve proper nouns, stable IDs, "
    "schema keys, code symbols, product names, and source-language aliases "
    "verbatim; translate generic labels and explanatory prose. Do not switch "
    "output language merely because the source does."
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

MEDIA_RULE = (
    "Media rule — props.image_links is REQUIRED, not optional: whenever the chunk "
    "contains an image URL (markdown ![alt](url), a link whose target ends in "
    ".jpg/.jpeg/.png/.webp, or a bare image URL) you MUST copy that URL VERBATIM "
    "into props.image_links of the ONE node the caption refers to. Use the caption "
    "or surrounding sentence to decide which node. Leaving image_links out of your "
    "output when the chunk contains image URLs is an incomplete extraction. The "
    "only permitted reason to skip a URL is that no node in your output corresponds "
    "to its caption. Never invent, shorten, complete, or reconstruct a URL, and "
    "never attach an image to a node its caption does not name. When the caption or "
    "text says what the picture shows, you MUST also set props.visual_desc on that "
    "node as one or two sentences. These two props exist only on node types that "
    "can be photographed — never add them to abstract types."
)

ENTITY_RESOLUTION_RULE = (
    "Entity resolution rule: when you recognize that two extracted nodes refer to "
    "the same real-world entity (e.g. 'person:user' and 'person:manhpt1' are the "
    "same person, or 'svc:api-gw' and 'svc:api-gateway' are the same service), "
    "emit a same_as edge (from=alias_id, to=canonical_id). Choose the more "
    "specific or human-readable id as canonical (to). Only use same_as for "
    "true identity — never for mere relationships (use depends_on, relates_to, "
    "etc. for those)."
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
    language: str = "en",
) -> str:
    """Build a constant system prompt including schema, language, and altitude rules.

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
        + (f"; id_prefix={spec.id_prefix}" if spec.id_prefix else "")
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
        OUTPUT_LANGUAGE_RULE.format(language=language),
        ALTITUDE_RULE,
        TEMPORAL_RULE,
        MEDIA_RULE,
        ENTITY_RESOLUTION_RULE,
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


def _strip_trailing_commas(s: str) -> str:
    """Remove trailing commas before closing braces/brackets.

    Many LLMs (DeepSeek, Qwen, some Ollama models) emit JSON with trailing
    commas like {"nodes": [...],} which is invalid JSON but trivially fixable.
    """
    return re.sub(r",(\s*[}\]])", r"\1", s)


def _find_balanced_json(raw: str, start_from: int = 0) -> str | None:
    """Find the first balanced JSON object in *raw* using a brace counter.

    More reliable than a greedy regex: handles nested objects, strings
    containing braces, and trailing prose. Returns None if no balanced
    object is found. Scans starting from *start_from*.
    """
    start = raw.find("{", start_from)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def _extract_json(raw: str, chunk: DocChunk) -> str:
    """Best-effort recover a JSON object from LLM output.

    response_format=json_object usually yields clean JSON, but some models wrap
    output in fences, prepend prose, or emit trailing commas. This function
    tries, in order:

    1. Direct parse (fast path for well-formed output)
    2. Strip markdown code fences + language tags, then parse
    3. Fix trailing commas, then parse
    4. Balanced-brace scan for the first JSON object, then parse
    5. Balanced-brace scan + trailing-comma fix (last resort)

    Raises ValueError (with chunk src) if all strategies fail.
    """
    s = raw.strip()

    # 0. Empty or whitespace-only output → treat as no extraction
    if not s:
        return '{"nodes": [], "edges": []}'

    # 1. Fast path: clean JSON
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences (```json, ```, etc.)
    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", s, re.DOTALL)
    if fence_match:
        s2 = fence_match.group(1).strip()
        try:
            json.loads(s2)
            return s2
        except json.JSONDecodeError:
            s = s2  # Use de-fenced version for subsequent strategies

    # 3. Fix trailing commas
    s3 = _strip_trailing_commas(s)
    if s3 != s:
        try:
            json.loads(s3)
            return s3
        except json.JSONDecodeError:
            pass

    # 4. Balanced-brace scan (handles prose prefix/suffix, nested objects)
    # Try each balanced match in order — prose like "for {project}:" may
    # produce a match that isn't valid JSON.
    offset = 0
    while True:
        balanced = _find_balanced_json(s, offset)
        if balanced is None:
            break
        try:
            json.loads(balanced)
            return balanced
        except json.JSONDecodeError:
            pass
        # Try with trailing-comma fix
        fixed = _strip_trailing_commas(balanced)
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            pass
        # Move past this match and try the next
        offset = s.find(balanced, offset) + len(balanced)

    raise ValueError(
        f"LLM returned non-JSON for {chunk.src}: "
        f"output starts with {raw[:200]!r}"
    )


def _normalize_node_id(
    raw_id: str,
    ntype: str,
    schema: Schema | None,
) -> str:
    """Enforce the canonical ``id_prefix:slug`` format for a node id.

    Strips any LLM-supplied prefix (``service:``, ``svc:``, ``srv:``, etc.)
    and replaces it with the schema-defined ``id_prefix`` for the node type.
    If the id has no ``:`` prefix, one is prepended.

    When the schema does not define ``id_prefix`` for a type (legacy/custom
    schemas), the id is returned unchanged so existing graphs are not
    destabilised.
    """
    if schema is None:
        return raw_id
    spec = schema.node_types.get(ntype)
    if spec is None or not spec.id_prefix:
        return raw_id
    prefix = spec.id_prefix
    # Strip everything before the first ':' if present, then re-prefix.
    slug = raw_id.split(":", 1)[1] if ":" in raw_id else raw_id
    return f"{prefix}:{slug}"


def parse_response(
    raw: str, chunk: DocChunk, schema: Schema | None = None,
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    data = json.loads(_extract_json(raw, chunk))

    # Build a remap table: LLM id → normalized id, so edge endpoints follow.
    id_remap: dict[str, str] = {}

    nodes: list[Node] = []
    for n in data.get("nodes", []):
        ntype = n.get("type")
        if schema is not None and not schema.is_valid_node_type(ntype):
            log.debug(
                "dropping node with unknown type=%r", ntype,
                extra={"event": "extract.unknown_node_type"},
            )
            continue
        props = dict(n.get("props", {}))
        for key in ("name", "title", "summary", "description"):
            if key in n and key not in props:
                props[key] = n[key]
        raw_id = n["id"]
        norm_id = _normalize_node_id(raw_id, ntype, schema)
        id_remap[raw_id] = norm_id
        if norm_id != raw_id:
            log.debug(
                "normalized node id %r → %r (type=%s)", raw_id, norm_id, ntype,
            )
        nodes.append(Node(
            id=norm_id,
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
            log.debug(
                "dropping edge with unknown type=%r", etype,
                extra={"event": "extract.unknown_edge_type"},
            )
            continue
        props = dict(e.get("props", {}))
        if "description" in e and "description" not in props:
            props["description"] = e["description"]
        edges.append(Edge(
            id="",                      # assigned deterministically in resolve
            type=etype,
            **{"from": id_remap.get(e["from"], e["from"])},
            to=id_remap.get(e["to"], e["to"]),
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
    """Maps (chunk_hash, schema_version) -> raw LLM response. Local only.

    Thread-safe: concurrent extraction workers can safely call get/set.
    """

    def __init__(self, path: Path) -> None:
        import threading
        self._lock = threading.Lock()
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
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, raw: str) -> None:
        with self._lock:
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
    language: str = "en",
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    model = getattr(provider, "model", "")
    system = build_system_prompt(
        schema, chunk.namespace, personal_ns, language=language,
    )
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
        f"language={language};"
        f"subject={chunk.namespace == personal_ns}"
    )
    key = cache.key(chunk, schema.version, model, prompt_variant)
    raw = cache.get(key)
    if raw is None:
        raw = provider.extract_json(system, chunk.text)
        cache.set(key, raw)
    return parse_response(raw, chunk, schema)

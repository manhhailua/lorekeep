"""Default config + schema used by `lorekeep init` to bootstrap a fresh home."""
from __future__ import annotations

DEFAULT_SCHEMA_V2 = {
    "version": 2,
    "node_types": {
        "service": {"props": {"name": "string", "lang": "string"}},
        "team": {"props": {"name": "string"}},
        "decision": {"props": {"title": "string"}},
        "project": {"props": {"name": "string", "status": "string"}},
        "person": {"props": {"name": "string", "role": "string"}},
        "tool": {"props": {"name": "string", "category": "string"}},
        "command": {"props": {"name": "string", "platform": "string"}},
        "concept": {"props": {"name": "string", "domain": "string"}},
        "note": {"props": {"title": "string", "topic": "string"}},
        "document": {"props": {"title": "string", "kind": "string"}},
    },
    "edge_types": {
        "depends_on": {"from": "service", "to": "service"},
        "decided_by": {"from": "decision", "to": "team"},
        "owns": {"from": "team", "to": "service"},
        "part_of": {"from": "service", "to": "project"},
        "uses": {"from": "service", "to": "tool"},
        "mentions": {"from": "note", "to": "concept"},
        "documents": {"from": "document", "to": "concept"},
        "describes": {"from": "note", "to": "service"},
        "relates_to": {"from": "concept", "to": "concept"},
    },
}

DEFAULT_SCHEMA_V3 = {
    # Ontology v2 (schema_version 3): work-context types bridging personal (me)
    # and team namespaces. Catch-all types (concept/tool/command/note) removed —
    # tokens that used to become those nodes are now attributes (see the altitude
    # rule in compile/extract.py). Validation is type-name only; from/to below are
    # guidance for the extractor, not a hard gate.
    "version": 3,
    "node_types": {
        # people / subject
        "person": {"props": {"name": "string", "handle": "string", "org": "string"}},
        "role": {"props": {"name": "string", "domain": "string"}},
        "skill": {"props": {"name": "string", "domain": "string", "level": "string"}},
        # knowledge
        "domain": {"props": {"name": "string", "description": "string"}},
        "preference": {"props": {"name": "string", "description": "string"}},
        "value": {"props": {"name": "string", "description": "string"}},
        "goal": {"props": {"title": "string", "timeframe": "string", "status": "string"}},
        # team / work
        "service": {"props": {"name": "string", "lang": "string", "status": "string"}},
        "project": {"props": {"name": "string", "status": "string", "start_date": "string"}},
        "decision": {"props": {"title": "string", "status": "string", "decided_on": "string"}},
        "team": {"props": {"name": "string", "org": "string"}},
        "document": {"props": {"title": "string", "kind": "string"}},
    },
    "edge_types": {
        # entity-centric (team)
        "depends_on": {"from": "service", "to": "service"},
        "part_of": {"from": "service", "to": "project"},
        "decided_by": {"from": "decision", "to": ["person", "team"]},
        # cross-ns bridge (subject -> team)
        "owns": {"from": ["person", "team"], "to": "service"},
        "contributes_to": {"from": "person", "to": "project"},
        "works_on": {"from": "person", "to": "project"},
        # subject knowledge
        "has_role": {"from": "person", "to": "role"},
        "has_skill": {"from": "person", "to": "skill"},
        "in_domain": {"from": ["role", "skill"], "to": "domain"},
        "pursues": {"from": "person", "to": "goal"},
        "holds_value": {"from": "person", "to": "value"},
        "member_of": {"from": "person", "to": "team"},
        "is_a": {"from": "service", "to": "domain"},
        "collaborates_with": {"from": "person", "to": "person"},
        "prefers": {"from": "person", "to": "preference"},
        # generic / doc
        "relates_to": {
            "from": ["person", "service", "project", "decision", "team", "domain", "skill", "role", "goal", "document"],
            "to": ["person", "service", "project", "decision", "team", "domain", "skill", "role", "goal", "document"],
        },
        "documents": {
            "from": "document",
            "to": ["service", "project", "decision", "domain"],
        },
        # entity resolution: merge alias nodes into canonical
        "same_as": {
            "from": ["person", "service", "project", "decision", "team", "domain", "skill", "role", "goal", "document"],
            "to": ["person", "service", "project", "decision", "team", "domain", "skill", "role", "goal", "document"],
        },
    },
}


# Ontology v2.1 (schema version 4) keeps the v3 type topology while adding a
# human-readable contract. The metadata is optional in the Pydantic models so
# custom and historical schemas remain loadable.
_NODE_DISPLAY = {
    "person": ("Person", "People", "name"),
    "role": ("Role", "Roles", "name"),
    "skill": ("Skill", "Skills", "name"),
    "domain": ("Domain", "Domains", "name"),
    "preference": ("Preference", "Preferences", "name"),
    "value": ("Value", "Values", "name"),
    "goal": ("Goal", "Goals", "title"),
    "service": ("Service", "Services", "name"),
    "project": ("Project", "Projects", "name"),
    "decision": ("Decision", "Decisions", "title"),
    "team": ("Team", "Teams", "name"),
    "document": ("Document", "Documents", "title"),
}

# Canonical slug prefix for each node type.  The LLM is told to use these
# prefixes, and ``parse_response`` enforces them deterministically so a
# ``service`` always gets ``svc:`` regardless of which abbreviation the
# model chose (``service:``, ``svc:``, ``srv:`` all normalize to ``svc:``).
_NODE_ID_PREFIX = {
    "person": "person",
    "role": "role",
    "skill": "skill",
    "domain": "domain",
    "preference": "pref",
    "value": "value",
    "goal": "goal",
    "service": "svc",
    "project": "prj",
    "decision": "dec",
    "team": "team",
    "document": "doc",
}

_EDGE_DISPLAY = {
    "depends_on": ("Depends on", "Depended on by"),
    "part_of": ("Part of", "Contains"),
    "decided_by": ("Decided by", "Made decision"),
    "owns": ("Owns", "Owned by"),
    "contributes_to": ("Contributes to", "Has contributor"),
    "works_on": ("Works on", "Has contributor"),
    "has_role": ("Has role", "Role held by"),
    "has_skill": ("Has skill", "Skill of"),
    "in_domain": ("In domain", "Includes"),
    "pursues": ("Pursues", "Pursued by"),
    "holds_value": ("Holds value", "Held by"),
    "member_of": ("Member of", "Has member"),
    "is_a": ("Is a", "Includes"),
    "collaborates_with": ("Collaborates with", "Collaborates with"),
    "prefers": ("Prefers", "Preferred by"),
    "relates_to": ("Relates to", "Relates to"),
    "documents": ("Documents", "Documented by"),
    "same_as": ("Same as", "Alias of"),
}

# Media props belong only to node types whose entities can be photographed.
# Offering them on abstract types (skill, value, goal, preference, ...) invites
# the extractor to invent a picture for an idea.
_MEDIA_NODE_TYPES = frozenset({"person", "service", "project", "team", "document"})
_MEDIA_PROPS = {
    "visual_desc": "string",
    "image_links": "string[]",
}

DEFAULT_SCHEMA = {
    **DEFAULT_SCHEMA_V3,
    "version": 4,
    "common_node_props": {
        "summary": "string",
        "description": "string",
    },
    "common_edge_props": {
        "description": "string",
    },
    "node_types": {
        name: {
            **spec,
            "props": {
                **spec.get("props", {}),
                **(_MEDIA_PROPS if name in _MEDIA_NODE_TYPES else {}),
            },
            "label": _NODE_DISPLAY[name][0],
            "plural": _NODE_DISPLAY[name][1],
            "display_prop": _NODE_DISPLAY[name][2],
            "id_prefix": _NODE_ID_PREFIX[name],
        }
        for name, spec in DEFAULT_SCHEMA_V3["node_types"].items()
    },
    "edge_types": {
        name: {
            **spec,
            "props": {},
            "label": _EDGE_DISPLAY[name][0],
            "inverse_label": _EDGE_DISPLAY[name][1],
        }
        for name, spec in DEFAULT_SCHEMA_V3["edge_types"].items()
    },
}

# Optional profile scaffold written to raw/<ns>/profile.md on first init.
# The user fills it in by hand (in Obsidian/Tolaria) — it is the editable
# source; the wiki is a derived view. The 'me' namespace is subject-centric,
# so this anchors extraction on the user and links their skills/domains/goals
# to team entities via cross-namespace edges.
DEFAULT_PROFILE_TEMPLATE = """\
# Profile

<!-- Personal context — fill in to anchor your knowledge graph. The 'me'
namespace is subject-centric: extraction anchors on you and links your
skills/domains/goals to team entities. Edit this file (Obsidian/Tolaria),
then `lorekeep compile` — the wiki reflects you. This raw/ file is the
source of truth; the wiki is a regenerable view. Delete any section you
leave blank. -->

## Role
<!-- e.g. AI/LLM Engineer, evaluation & guardrail -->

## Domains
<!-- knowledge areas you're strong in, one per line:
RAG evaluation, GCP IAM, Confluence platform, ... -->

## Skills
<!-- one per line: name (level: beginner | practitioner | expert) -->

## Goals
<!-- current objectives / OKRs -->

## Preferences
<!-- working style, e.g. terse comms, confirm before destructive ops -->

## Values
<!-- principles that shape your decisions -->
"""

DEFAULT_CONFIG_YAML = """\
provider:
  model: openai/gpt-4o-mini
  api_base: null
  api_key_env: OPENAI_API_KEY
  api_key: null
  temperature: 0.0
  timeout_seconds: 120
  max_retries: 2
compile:
  chunk_lines: 60
  language: en
  max_workers: 4
  flush_interval: 10
  # Fetch every props.image_links URL after resolve and drop the dead ones.
  # The only network access compile makes — set false to stay fully offline.
  check_image_links: true
  image_check_timeout: 10.0
  image_check_workers: 8
namespaces:
  read: ["*"]
  write: me
bugreport:
  enabled: true
  repo: manhhailua/lorekeep
  token_env: LOREKEEP_GITHUB_TOKEN
  labels: [auto-reported]
agents:
  auto_wire: true
  wire_scope: user
  wire_interval_seconds: 900
  enabled: [claude, codex, cursor, opencode, grok, qoder, copilot, cmd]
  watch_transcripts: true
  transcript_max_batches: 20
  transcript_max_chars: 20000
  transcript_retain_sessions: 5
  session_end_idle_seconds: 300
  deep_import: false
  self_heal: true
  auto_backup: true
backup:
  branch: main
  auto_resolve_durable: false
install_source: pypi
"""

SAMPLE_DOC = """\
# Lorekeep sample

This file demonstrates how raw markdown becomes a knowledge graph.
Delete it once you add your own docs.

## Services

**api-gateway** is the main entry point, written in Go.

**auth-service** handles authentication, written in Python.

The api-gateway depends on auth-service for token validation.

## Decisions

ADR-001: Adopt api-gateway pattern for all client traffic.
This was decided by the backend team.
"""

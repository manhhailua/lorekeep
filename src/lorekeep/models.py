"""Core data models for Lorekeep. The shared contract across compile + eval."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DocChunk(BaseModel):
    """A slice of a raw document, with provenance back to path:line."""
    model_config = ConfigDict(frozen=True)

    path: str
    start_line: int          # 1-based
    end_line: int
    text: str
    namespace: str           # e.g. "teams/backend"

    @property
    def src(self) -> str:
        return f"{self.path}:{self.start_line}"

    @property
    def hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.path.encode("utf-8"))
        h.update(b"\n")
        h.update(self.text.encode("utf-8"))
        return h.hexdigest()


class Node(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["node"] = "node"
    id: str
    type: str
    ns: tuple[str, ...]
    valid_from: date | None = None
    valid_to: date | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    src: tuple[str, ...] = Field(default_factory=tuple)

    def to_json_line(self) -> str:
        d = self.model_dump(mode="json", by_alias=True)
        return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    kind: Literal["edge"] = "edge"
    id: str
    type: str
    from_: str = Field(alias="from")
    to: str
    ns: tuple[str, ...]
    valid_from: date | None = None
    valid_to: date | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    src: tuple[str, ...] = Field(default_factory=tuple)

    def to_json_line(self) -> str:
        d = self.model_dump(mode="json", by_alias=True)
        return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


Fact = Node | Edge


class TypeSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    props: dict[str, str] = Field(default_factory=dict)


class EndpointSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int
    node_types: dict[str, TypeSpec]
    edge_types: dict[str, EndpointSpec]

    @classmethod
    def load(cls, data: dict[str, Any]) -> "Schema":
        return cls.model_validate(data)

    def is_valid_node_type(self, t: str) -> bool:
        return t in self.node_types

    def is_valid_edge_type(self, t: str) -> bool:
        return t in self.edge_types


class CompileError(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    line: int
    message: str


class QuarantineItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    fact: dict[str, Any]
    reason: str


class JournalEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fact: dict[str, Any]
    agent: str
    ns: str
    confidence: float
    proposed_at: str
    status: str = "pending"


class ReviewItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    fact_id: str
    reason: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    chunk_count: int
    node_count: int
    edge_count: int
    run_id: str
    facts_hash: str
    compiled_at: str = ""
    chunk_hashes: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[CompileError] = Field(default_factory=list)
    quarantine: list[QuarantineItem] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)
    merged_count: int = 0
    quarantined_count: int = 0
    flagged_count: int = 0

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        return cls.model_validate(json.loads(text))


def now_iso() -> str:
    """UTC timestamp for Manifest.compiled_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

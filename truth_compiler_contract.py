"""Generic evidence -> policy -> derived-reality contract for Truth Compiler.

This module is deliberately conservative. It never manufactures evidence and it
never treats multiple observations from the same independence group as separate
support. The only terminal verdicts are VERIFIED, UNKNOWN, and DENIED.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import sys
from typing import Any, Dict, List, Mapping, Sequence


SCHEMA_VERSION = "truth.compiler.contract.v1"
COMPILER_VERSION = "1.0.0"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class EvidenceStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


class TruthVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    DENIED = "DENIED"


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    status: EvidenceStatus
    source: str
    independence_group: str
    provenance: Dict[str, Any]
    facts: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "EvidenceItem":
        required = {"evidence_id", "status", "source", "independence_group", "provenance"}
        missing = required - set(row)
        if missing:
            raise ValueError(f"evidence missing fields: {sorted(missing)}")
        evidence_id = str(row["evidence_id"]).strip()
        if not evidence_id:
            raise ValueError("evidence_id is required")
        return cls(
            evidence_id=evidence_id,
            status=EvidenceStatus(str(row["status"]).upper()),
            source=str(row["source"]),
            independence_group=str(row["independence_group"]),
            provenance=dict(row.get("provenance") or {}),
            facts=dict(row.get("facts") or {}),
        )

    def provenance_valid(self) -> bool:
        digest = str(self.provenance.get("sha256") or "")
        return len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest.lower())


@dataclass(frozen=True)
class EvidenceVector:
    items: tuple[EvidenceItem, ...]

    @classmethod
    def from_rows(cls, rows: Sequence[Mapping[str, Any]]) -> "EvidenceVector":
        items = tuple(EvidenceItem.from_dict(row) for row in rows)
        evidence_ids = [item.evidence_id for item in items]
        duplicate_ids = sorted({evidence_id for evidence_id in evidence_ids if evidence_ids.count(evidence_id) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate evidence_id values are not allowed: {duplicate_ids}")
        return cls(items)

    def independent_groups(self, status: EvidenceStatus) -> set[str]:
        return {
            item.independence_group
            for item in self.items
            if item.status == status and item.provenance_valid()
        }

    def unknown_items(self) -> tuple[str, ...]:
        return tuple(sorted(item.evidence_id for item in self.items if item.status == EvidenceStatus.UNKNOWN))

    def invalid_provenance_items(self) -> tuple[str, ...]:
        return tuple(sorted(item.evidence_id for item in self.items if not item.provenance_valid()))

    def facts(self) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        conflicts: Dict[str, List[Any]] = {}
        for item in sorted(self.items, key=lambda x: x.evidence_id):
            if item.status != EvidenceStatus.SUPPORTED or not item.provenance_valid():
                continue
            for key, value in sorted(item.facts.items()):
                if key not in merged:
                    merged[key] = value
                elif merged[key] != value:
                    conflicts.setdefault(key, [merged[key]])
                    if value not in conflicts[key]:
                        conflicts[key].append(value)
        for key, values in conflicts.items():
            merged[key] = {"conflict": values}
        return merged


@dataclass(frozen=True)
class Policy:
    authority_allowed: bool
    min_independent_support: int = 1
    max_contradictions: int = 0
    required_fact_keys: tuple[str, ...] = ()

    @staticmethod
    def _strict_int(row: Mapping[str, Any], key: str, default: int, minimum: int) -> int:
        value = row.get(key, default)
        # bool is a subclass of int in Python, so reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"policy.{key} must be an integer")
        if value < minimum:
            raise ValueError(f"policy.{key} must be >= {minimum}")
        return value

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Policy":
        authority_allowed = row.get("authority_allowed", False)
        if not isinstance(authority_allowed, bool):
            raise ValueError("policy.authority_allowed must be a boolean")

        raw_required_fact_keys = row.get("required_fact_keys", [])
        if not isinstance(raw_required_fact_keys, list):
            raise ValueError("policy.required_fact_keys must be a list of strings")
        required_fact_keys: list[str] = []
        for value in raw_required_fact_keys:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("policy.required_fact_keys must contain non-empty strings")
            required_fact_keys.append(value.strip())

        return cls(
            authority_allowed=authority_allowed,
            min_independent_support=cls._strict_int(row, "min_independent_support", 1, 1),
            max_contradictions=cls._strict_int(row, "max_contradictions", 0, 0),
            required_fact_keys=tuple(sorted(set(required_fact_keys))),
        )


@dataclass(frozen=True)
class DerivedReality:
    schema_version: str
    compiler_version: str
    claim_id: str
    verdict: TruthVerdict
    evidence_state: str
    policy_state: str
    independent_support_groups: tuple[str, ...]
    contradiction_groups: tuple[str, ...]
    unknown_evidence_ids: tuple[str, ...]
    invalid_provenance_ids: tuple[str, ...]
    missing_fact_keys: tuple[str, ...]
    reasons: tuple[str, ...]
    input_sha256: str
    result_sha256: str

    def to_dict(self) -> Dict[str, Any]:
        row = asdict(self)
        row["verdict"] = self.verdict.value
        return row


class TruthCompiler:
    """Compile supplied evidence and policy into a conservative derived reality."""

    def compile(self, request: Mapping[str, Any]) -> DerivedReality:
        if str(request.get("schema_version") or "") != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        claim_id = str(request.get("claim_id") or "").strip()
        if not claim_id:
            raise ValueError("claim_id is required")
        raw_evidence = request.get("evidence")
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence must be a list")
        vector = EvidenceVector.from_rows(raw_evidence)
        policy = Policy.from_dict(dict(request.get("policy") or {}))
        input_sha = sha256_json(request)

        support = tuple(sorted(vector.independent_groups(EvidenceStatus.SUPPORTED)))
        contradictions = tuple(sorted(vector.independent_groups(EvidenceStatus.CONTRADICTED)))
        unknown_ids = vector.unknown_items()
        invalid_provenance = vector.invalid_provenance_items()
        facts = vector.facts()
        missing_facts = tuple(sorted(key for key in policy.required_fact_keys if key not in facts or facts[key] is not True))

        reasons: List[str] = []
        if not policy.authority_allowed:
            policy_state = "DENIED"
            evidence_state = "UNRESOLVED"
            verdict = TruthVerdict.DENIED
            reasons.append("policy_authority_denied")
        else:
            policy_state = "ALLOWED"
            if invalid_provenance:
                reasons.append("invalid_or_missing_provenance")
            if len(contradictions) > policy.max_contradictions:
                reasons.append("contradiction_limit_exceeded")
            if len(support) < policy.min_independent_support:
                reasons.append("insufficient_independent_support")
            if missing_facts:
                reasons.append("required_facts_missing_or_false")
            if unknown_ids:
                reasons.append("unknown_evidence_present")

            evidence_ok = (
                not invalid_provenance
                and len(contradictions) <= policy.max_contradictions
                and len(support) >= policy.min_independent_support
                and not missing_facts
                and not unknown_ids
            )
            evidence_state = "VERIFIED" if evidence_ok else "UNKNOWN"
            verdict = TruthVerdict.VERIFIED if evidence_ok else TruthVerdict.UNKNOWN
            if evidence_ok:
                reasons.append("evidence_and_policy_verified")

        hash_core = {
            "schema_version": SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "claim_id": claim_id,
            "verdict": verdict.value,
            "evidence_state": evidence_state,
            "policy_state": policy_state,
            "independent_support_groups": support,
            "contradiction_groups": contradictions,
            "unknown_evidence_ids": unknown_ids,
            "invalid_provenance_ids": invalid_provenance,
            "missing_fact_keys": missing_facts,
            "reasons": tuple(reasons),
            "input_sha256": input_sha,
        }
        result_sha = sha256_json(hash_core)
        return DerivedReality(
            schema_version=SCHEMA_VERSION,
            compiler_version=COMPILER_VERSION,
            claim_id=claim_id,
            verdict=verdict,
            evidence_state=evidence_state,
            policy_state=policy_state,
            independent_support_groups=support,
            contradiction_groups=contradictions,
            unknown_evidence_ids=unknown_ids,
            invalid_provenance_ids=invalid_provenance,
            missing_fact_keys=missing_facts,
            reasons=tuple(reasons),
            input_sha256=input_sha,
            result_sha256=result_sha,
        )


def compile_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    return TruthCompiler().compile(request).to_dict()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        request = json.load(sys.stdin)
        result = compile_request(request)
        sys.stdout.write(canonical_json(result) + "\n")
        return 0
    except Exception as exc:
        error = {
            "schema_version": SCHEMA_VERSION,
            "verdict": TruthVerdict.UNKNOWN.value,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        sys.stdout.write(canonical_json(error) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

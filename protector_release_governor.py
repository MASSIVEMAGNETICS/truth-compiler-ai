"""Bounded Protector/Release Governor for Truth Compiler.

This module is deliberately non-authoritative. It can enforce workflow gates,
record tamper-evident receipts, and authorize explicitly leased capabilities.
It cannot emit PASS/FAIL, production authorization, or truth claims.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class GovernorError(RuntimeError):
    """Base error for fail-closed governor operations."""


class LeaseDenied(GovernorError):
    """Raised when a capability lease is absent, expired, revoked, or scoped wrong."""


class InvalidTransition(GovernorError):
    """Raised when a release state transition is not allowed."""


class ReleaseState(str, Enum):
    CREATED = "CREATED"
    ASSETS_LOGGED = "ASSETS_LOGGED"
    LORE_UPDATED = "LORE_UPDATED"
    HUB_LINKED = "HUB_LINKED"
    CTA_DEFINED = "CTA_DEFINED"
    FUNNEL_VERIFIED = "FUNNEL_VERIFIED"
    ARCHIVED = "ARCHIVED"


_TRANSITIONS = {
    ReleaseState.CREATED: ReleaseState.ASSETS_LOGGED,
    ReleaseState.ASSETS_LOGGED: ReleaseState.LORE_UPDATED,
    ReleaseState.LORE_UPDATED: ReleaseState.HUB_LINKED,
    ReleaseState.HUB_LINKED: ReleaseState.CTA_DEFINED,
    ReleaseState.CTA_DEFINED: ReleaseState.FUNNEL_VERIFIED,
    ReleaseState.FUNNEL_VERIFIED: ReleaseState.ARCHIVED,
}


@dataclass(frozen=True)
class CapabilityLease:
    lease_id: str
    subject: str
    capability: str
    scope: str
    expires_at: float
    issued_at: float
    issuer: str
    revoked: bool = False

    def permits(self, subject: str, capability: str, scope: str, now: Optional[float] = None) -> bool:
        current = time.time() if now is None else now
        return (
            not self.revoked
            and self.subject == subject
            and self.capability == capability
            and self.scope == scope
            and current < self.expires_at
        )


@dataclass(frozen=True)
class SimulationOutcome:
    branch_id: str
    hypothesis: str
    score: float
    risk: float
    seed: int


class DeterministicSimulator:
    """Reproducible ranking signal; never an authority source."""

    def __init__(self, branches: int = 6) -> None:
        if not isinstance(branches, int) or branches < 1 or branches > 256:
            raise ValueError("branches must be an integer in [1, 256]")
        self.branches = branches

    def run(self, hypotheses: Iterable[str], seed: int) -> Tuple[SimulationOutcome, ...]:
        if not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        choices = tuple(h for h in hypotheses if isinstance(h, str) and h.strip())
        if not choices:
            raise ValueError("at least one non-empty hypothesis is required")
        rng = random.Random(seed)
        outcomes = []
        for index in range(self.branches):
            hypothesis = choices[rng.randrange(len(choices))]
            score = round(rng.random(), 6)
            risk = round(rng.random(), 6)
            outcomes.append(SimulationOutcome(f"b{index:04d}", hypothesis, score, risk, seed))
        return tuple(sorted(outcomes, key=lambda item: (item.score - item.risk, item.branch_id), reverse=True))


class ProtectorReleaseGovernor:
    """SQLite-backed release governor with strict transitions and hash receipts."""

    ASSET_TYPES = frozenset({"audio", "lore", "build"})

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._schema()

    def close(self) -> None:
        self.conn.close()

    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS releases (
                release_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                track_ref TEXT NOT NULL,
                state TEXT NOT NULL,
                lore_ref TEXT,
                hub_url TEXT,
                cta TEXT,
                created_at REAL NOT NULL,
                verified_at REAL
            );
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                release_id TEXT NOT NULL REFERENCES releases(release_id),
                asset_type TEXT NOT NULL,
                description TEXT NOT NULL,
                path_or_url TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL,
                previous_hash TEXT NOT NULL,
                receipt_hash TEXT UNIQUE NOT NULL
            );
            """
        )

    @staticmethod
    def _text(value: Any, field: str, max_len: int = 4096) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > max_len:
            raise ValueError(f"{field} must be a non-empty string of at most {max_len} characters")
        return value.strip()

    def _receipt(self, event_type: str, payload: Dict[str, Any]) -> str:
        event_type = self._text(event_type, "event_type", 128)
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        now = time.time()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self.conn.execute(
                "SELECT receipt_hash FROM receipts ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous[0] if previous else "GENESIS"
            receipt_id = "rcpt_" + uuid.uuid4().hex
            material = "|".join((previous_hash, receipt_id, event_type, canonical_payload, f"{now:.6f}"))
            receipt_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            self.conn.execute(
                "INSERT INTO receipts(receipt_id,event_type,payload,created_at,previous_hash,receipt_hash) VALUES(?,?,?,?,?,?)",
                (receipt_id, event_type, canonical_payload, now, previous_hash, receipt_hash),
            )
            self.conn.execute("COMMIT")
            return receipt_hash
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def verify_receipts(self) -> bool:
        rows = self.conn.execute("SELECT * FROM receipts ORDER BY sequence").fetchall()
        previous = "GENESIS"
        for row in rows:
            material = "|".join((previous, row["receipt_id"], row["event_type"], row["payload"], f"{row['created_at']:.6f}"))
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if row["previous_hash"] != previous or row["receipt_hash"] != expected:
                return False
            previous = row["receipt_hash"]
        return True

    def issue_lease(self, subject: str, capability: str, scope: str, ttl_seconds: float) -> CapabilityLease:
        subject = self._text(subject, "subject", 256)
        capability = self._text(capability, "capability", 256)
        scope = self._text(scope, "scope", 1024)
        if not isinstance(ttl_seconds, (int, float)) or not 0 < ttl_seconds <= 3600:
            raise ValueError("ttl_seconds must be in (0, 3600]")
        now = time.time()
        lease = CapabilityLease("lease_" + uuid.uuid4().hex, subject, capability, scope, now + ttl_seconds, now, "protector")
        self._receipt("CapabilityLeaseIssued", {"lease_id": lease.lease_id, "subject": subject, "capability": capability, "scope": scope})
        return lease

    def require_lease(self, lease: Optional[CapabilityLease], subject: str, capability: str, scope: str) -> None:
        if lease is None or not lease.permits(subject, capability, scope):
            self._receipt("CapabilityDenied", {"subject": subject, "capability": capability, "scope": scope})
            raise LeaseDenied(f"capability denied: {capability} on {scope}")

    def authorize_device_action(self, lease: Optional[CapabilityLease], subject: str, action: str, device_id: str) -> str:
        device_id = self._text(device_id, "device_id", 256)
        action = self._text(action, "action", 128)
        self.require_lease(lease, subject, f"device.{action}", device_id)
        return self._receipt("DeviceActionAuthorized", {"subject": subject, "action": action, "device_id": device_id})

    def _release(self, release_id: str) -> sqlite3.Row:
        release_id = self._text(release_id, "release_id", 128)
        row = self.conn.execute("SELECT * FROM releases WHERE release_id=?", (release_id,)).fetchone()
        if row is None:
            raise KeyError(f"release not found: {release_id}")
        return row

    def _advance(self, release_id: str, expected: ReleaseState, new: ReleaseState, **fields: Any) -> None:
        row = self._release(release_id)
        if row["state"] != expected.value:
            raise InvalidTransition(f"{release_id}: expected {expected.value}, found {row['state']}")
        assignments = ["state=?"]
        values: List[Any] = [new.value]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            values.append(value)
        values.append(release_id)
        self.conn.execute(f"UPDATE releases SET {', '.join(assignments)} WHERE release_id=?", values)
        self._receipt("ReleaseStateChanged", {"release_id": release_id, "from": expected.value, "to": new.value})

    def start_release(self, name: str, track_ref: str = "") -> str:
        name = self._text(name, "name", 512)
        track_ref = track_ref.strip() if isinstance(track_ref, str) else ""
        open_release = self.conn.execute(
            "SELECT release_id,state FROM releases WHERE state NOT IN (?,?) ORDER BY created_at DESC LIMIT 1",
            (ReleaseState.FUNNEL_VERIFIED.value, ReleaseState.ARCHIVED.value),
        ).fetchone()
        if open_release:
            raise InvalidTransition(f"unverified release blocks new release: {open_release['release_id']}")
        release_id = "rel_" + uuid.uuid4().hex
        now = time.time()
        self.conn.execute(
            "INSERT INTO releases VALUES(?,?,?,?,?,?,?,?,?)",
            (release_id, name, track_ref, ReleaseState.CREATED.value, None, None, None, now, None),
        )
        self._receipt("ReleaseStarted", {"release_id": release_id, "name": name, "track_ref": track_ref})
        return release_id

    def log_asset(self, release_id: str, asset_type: str, description: str, path_or_url: str = "") -> None:
        row = self._release(release_id)
        if row["state"] != ReleaseState.CREATED.value:
            raise InvalidTransition("assets can only be logged in CREATED state")
        asset_type = self._text(asset_type, "asset_type", 32).lower()
        if asset_type not in self.ASSET_TYPES:
            raise ValueError("asset_type must be audio, lore, or build")
        description = self._text(description, "description")
        path_or_url = path_or_url.strip() if isinstance(path_or_url, str) else ""
        self.conn.execute("INSERT INTO assets VALUES(?,?,?,?,?,?)", ("ast_" + uuid.uuid4().hex, release_id, asset_type, description, path_or_url, time.time()))
        self._receipt("AssetLogged", {"release_id": release_id, "asset_type": asset_type, "description": description})

    def verify_assets(self, release_id: str) -> None:
        counts = {row["asset_type"]: row["count"] for row in self.conn.execute("SELECT asset_type,COUNT(*) count FROM assets WHERE release_id=? GROUP BY asset_type", (release_id,))}
        if counts != {"audio": 10, "build": 10, "lore": 10}:
            raise InvalidTransition(f"asset gate requires exactly 10/10/10, found {counts}")
        self._advance(release_id, ReleaseState.CREATED, ReleaseState.ASSETS_LOGGED)

    def update_lore(self, release_id: str, lore_ref: str) -> None:
        lore_ref = self._text(lore_ref, "lore_ref")
        self._advance(release_id, ReleaseState.ASSETS_LOGGED, ReleaseState.LORE_UPDATED, lore_ref=lore_ref)

    def link_hub(self, release_id: str, hub_url: str) -> None:
        hub_url = self._text(hub_url, "hub_url")
        self._advance(release_id, ReleaseState.LORE_UPDATED, ReleaseState.HUB_LINKED, hub_url=hub_url)

    def define_cta(self, release_id: str, cta: str) -> None:
        cta = self._text(cta, "cta")
        self._advance(release_id, ReleaseState.HUB_LINKED, ReleaseState.CTA_DEFINED, cta=cta)

    def verify_and_lock(self, release_id: str) -> None:
        row = self._release(release_id)
        if row["state"] != ReleaseState.CTA_DEFINED.value:
            raise InvalidTransition("funnel lock requires CTA_DEFINED state")
        self._advance(release_id, ReleaseState.CTA_DEFINED, ReleaseState.FUNNEL_VERIFIED, verified_at=time.time())



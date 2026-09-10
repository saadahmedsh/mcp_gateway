"""Durable append-only audit logging with a SHA-256 hash chain."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gateway.models import AttemptRecord, AuditRecord, Principal, RepairAttemptRecord

_GENESIS_HASH = "0" * 64


def _canonical_json(value: dict[str, Any]) -> bytes:
    """Serialize an audit payload deterministically for hashing."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _record_hash(payload: dict[str, Any]) -> str:
    """Compute the digest for one canonical audit payload."""

    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class AuditLogger:
    """Append immutable JSONL records while preserving their hash chain."""

    def __init__(self, path: Path) -> None:
        """Initialize a logger and resume the chain from an existing file."""

        self._path = path
        self._lock = asyncio.Lock()
        self._last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        """Read the final record hash or return the genesis hash."""

        if not self._path.exists():
            return _GENESIS_HASH
        last_line = ""
        with self._path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    last_line = line
        if not last_line:
            return _GENESIS_HASH
        payload = json.loads(last_line)
        value = payload.get("record_hash")
        if not isinstance(value, str):
            return _GENESIS_HASH
        return value

    def _append_sync(self, fields: dict[str, Any]) -> AuditRecord:
        """Append one record synchronously with an fsync durability barrier."""

        timestamp = fields.get("timestamp") or datetime.now(UTC)
        timestamp_value = (
            timestamp
            if isinstance(timestamp, datetime)
            else datetime.fromisoformat(str(timestamp))
        )
        payload: dict[str, Any] = {
            **fields,
            "timestamp": timestamp_value,
            "previous_hash": self._last_hash,
            "record_hash": "pending",
        }
        pending = AuditRecord.model_validate(payload)
        unsigned = pending.model_dump(mode="json")
        del unsigned["record_hash"]
        digest = _record_hash(unsigned)
        record = pending.model_copy(update={"record_hash": digest})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(record.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._last_hash = digest
        return record

    async def append(
        self,
        *,
        call_id: str,
        session_id: str,
        trace_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        policy_input: dict[str, Any] | None = None,
        policy_decision: str,
        matched_rule: str | None,
        approver_identity: str | None,
        sandbox_runtime: str,
        attempts: list[AttemptRecord],
        repair_attempts: list[RepairAttemptRecord] | None = None,
        outcome: str,
        duration_ms: float,
        principal: Principal | None = None,
        timestamp: datetime | None = None,
    ) -> AuditRecord:
        """Append one completed tool-call event without blocking the event loop."""

        fields: dict[str, Any] = {
            "call_id": call_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "policy_input": policy_input or {},
            "policy_decision": policy_decision,
            "matched_rule": matched_rule,
            "approver_identity": approver_identity,
            "sandbox_runtime": sandbox_runtime,
            "attempts": attempts,
            "repair_attempts": repair_attempts or [],
            "outcome": outcome,
            "duration_ms": duration_ms,
            "principal": principal,
        }
        if timestamp is not None:
            fields["timestamp"] = timestamp
        async with self._lock:
            return await asyncio.to_thread(self._append_sync, fields)

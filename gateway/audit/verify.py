"""Verification helpers for the append-only audit hash chain."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from gateway.audit.log import _GENESIS_HASH, _canonical_json
from gateway.errors import AuditIntegrityError


def verify_audit_file(path: Path) -> bool:
    """Return whether every record in an audit file forms a valid chain."""

    previous_hash = _GENESIS_HASH
    if not path.exists():
        return True
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise AuditIntegrityError(
                    f"Invalid JSON on audit line {line_number}"
                ) from error
            if not isinstance(payload, dict):
                raise AuditIntegrityError(f"Audit line {line_number} is not an object")
            if payload.get("previous_hash") != previous_hash:
                raise AuditIntegrityError(f"Broken audit link on line {line_number}")
            stored_hash = payload.get("record_hash")
            if not isinstance(stored_hash, str):
                raise AuditIntegrityError(f"Missing record hash on line {line_number}")
            unsigned: dict[str, Any] = dict(payload)
            del unsigned["record_hash"]
            expected_hash = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
            if stored_hash != expected_hash:
                raise AuditIntegrityError(
                    f"Tampered audit record on line {line_number}"
                )
            previous_hash = stored_hash
    return True


def main() -> None:
    """Verify the configured audit file or a path supplied on the command line."""

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/audit.jsonl")
    verify_audit_file(path)
    sys.stdout.write(f"audit chain valid: {path}\n")


if __name__ == "__main__":
    main()

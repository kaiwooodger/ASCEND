"""Canonical hashing, run identifiers, and shared provenance records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ascend import __version__


_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def git_commit() -> str:
    """Return the immutable source commit or an explicit unavailable state."""
    configured = os.environ.get("ASCEND_GIT_COMMIT", "").strip().lower()
    if _COMMIT_PATTERN.fullmatch(configured):
        return configured
    try:
        root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        discovered = completed.stdout.strip().lower()
        return discovered if _COMMIT_PATTERN.fullmatch(discovered) else "unavailable"
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unavailable"


def software_identity() -> dict[str, str]:
    """Return the release and source identity stored with research outputs."""
    return {"ascend_version": __version__, "git_commit": git_commit()}


def canonical_hash(value: Any) -> str:
    """Handle canonical hash for the enclosing ASCEND workflow."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: str | Path) -> str:
    """Handle file hash for the enclosing ASCEND workflow."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_id(layer: str) -> str:
    """Execute id and return its explicit calculation state and evidence."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"ASCEND_{layer.upper()}_{stamp}"


def base_provenance(configuration_hash: str, parent_layer1_run_id: str | None = None) -> dict[str, Any]:
    """Handle base provenance for the enclosing ASCEND workflow."""
    return {
        **software_identity(),
        "configuration_hash": configuration_hash,
        "parent_layer1_run_id": parent_layer1_run_id,
        "calculation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

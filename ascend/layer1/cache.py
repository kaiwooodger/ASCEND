"""Immutable case-local Layer 1 cache publication, verification, and materialization."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import shutil
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ascend.validation.provenance import canonical_hash, file_hash


CACHE_SCHEMA_VERSION = "ASCEND-L1-cache-v1"
LAYER1_RESULT_SCHEMA_VERSION = "ASCEND-Layer1-result-v2"
RASTERISATION_ALGORITHM_VERSION = "BARAT-L1-RASTER-CTNN-GAPSAFE-v4"


def fsync_path(path: Path) -> None:
    """Flush a file or directory so an atomic rename survives process failure."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_publish_directory(staging: Path, destination: Path) -> None:
    """Publish a complete directory with one same-filesystem atomic rename.

    Callers construct all artifacts below ``staging``.  The final run is not
    visible at ``destination`` until every file and directory entry is durable.
    """
    if destination.exists():
        raise FileExistsError(destination)
    for file_path in staging.rglob("*"):
        if file_path.is_file():
            fsync_path(file_path)
    fsync_path(staging)
    os.replace(staging, destination)
    fsync_path(destination.parent)


def cleanup_abandoned(parent: Path) -> None:
    """Remove unpublished sibling staging directories left by interrupted runs."""
    if not parent.is_dir():
        return
    for path in parent.glob(".tmp-*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _clone_file(source: Path, destination: Path) -> bool:
    """Attempt an independent copy-on-write clone on macOS or Linux."""
    if sys.platform == "darwin":
        try:
            library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            clonefile = library.clonefile
            clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            clonefile.restype = ctypes.c_int
            return clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0
        except Exception:
            return False
    if sys.platform.startswith("linux"):
        try:
            with source.open("rb") as reader, destination.open("wb") as writer:
                fcntl.ioctl(writer.fileno(), 0x40049409, reader.fileno())  # FICLONE
            return True
        except OSError as exc:
            if destination.exists():
                destination.unlink()
            if exc.errno not in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL}:
                raise
    return False


def independent_copy(source: Path, destination: Path) -> str:
    """Materialise an artifact without hard links and verify its content hash."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    method = "reflink" if _clone_file(source, destination) else "copy"
    if method == "copy":
        shutil.copy2(source, destination)
    if file_hash(source) != file_hash(destination):
        destination.unlink(missing_ok=True)
        raise ValueError(f"Materialised cache artifact hash mismatch: {source.name}")
    return method


def copy_tree_independent(source: Path, destination: Path) -> str:
    """Copy a cached run so later cache deletion cannot damage formal results."""
    methods: set[str] = set()
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(exist_ok=True)
        elif path.name != "cache_entry.json":
            methods.add(independent_copy(path, target))
    return "reflink" if methods == {"reflink"} else "copy_or_mixed"


def entry_artifacts(directory: Path) -> dict[str, dict[str, Any]]:
    """Return the immutable artifact hash and size inventory for an entry."""
    return {
        str(path.relative_to(directory)): {"sha256": file_hash(path), "size_bytes": path.stat().st_size}
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "cache_entry.json"
    }


def verify_entry(directory: Path) -> tuple[bool, str | None]:
    """Verify cache schema and every recorded artifact before reuse."""
    manifest_path = directory / "cache_entry.json"
    if not manifest_path.is_file():
        return False, "cache_entry_manifest_missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False, "cache_entry_manifest_invalid"
    if manifest.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        return False, "cache_schema_version_mismatch"
    for relative, expected in manifest.get("artifacts", {}).items():
        path = directory / relative
        if not path.is_file() or file_hash(path) != expected.get("sha256"):
            return False, f"cache_artifact_mismatch:{relative}"
    return True, None


def make_read_only(directory: Path) -> None:
    """Apply filesystem read-only permissions after atomic cache publication."""
    for path in directory.rglob("*"):
        mode = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        if path.is_dir():
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        path.chmod(mode)
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)


def cache_key(payload: dict[str, Any]) -> str:
    """Hash all Layer 1 inputs, bindings, configuration, and version contracts."""
    return canonical_hash({"cache_schema_version": CACHE_SCHEMA_VERSION, **payload})


class Layer1Cache:
    """Manage immutable Layer 1 entries inside one ASCEND case directory."""
    def __init__(self, case_root: Path) -> None:
        self.root = case_root / "cache" / "layer1"
        self.root.mkdir(parents=True, exist_ok=True)
        cleanup_abandoned(self.root)

    def path(self, key: str) -> Path:
        """Return the opaque entry location for ``key``."""
        return self.root / key

    def inspect(self) -> list[dict[str, Any]]:
        """Report cache size, validity, versions, and creation metadata."""
        records = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir() or path.name.startswith(".tmp-"):
                continue
            valid, reason = verify_entry(path)
            size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
            metadata = {}
            try:
                metadata = json.loads((path / "cache_entry.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            records.append({
                "cache_key": path.name, "valid": valid, "reason": reason, "size_bytes": size,
                "created_utc": metadata.get("created_utc"), "versions": metadata.get("versions", {}),
                "artifact_count": len(metadata.get("artifacts", {})),
            })
        return records

    def clear(self, confirmed: bool = False) -> int:
        """Delete cache entries only after explicit caller confirmation."""
        if not confirmed:
            raise ValueError("Cache clearing requires explicit confirmation.")
        count = 0
        for path in list(self.root.iterdir()):
            if path.is_dir():
                for item in path.rglob("*"):
                    if item.exists():
                        item.chmod(stat.S_IRWXU)
                path.chmod(stat.S_IRWXU)
                shutil.rmtree(path)
                count += 1
        return count

    def publish(self, key: str, formal_run: Path, versions: dict[str, str]) -> Path:
        """Publish a verified, immutable copy of a completed formal run."""
        destination = self.path(key)
        if destination.exists():
            valid, _reason = verify_entry(destination)
            if valid:
                return destination
            for item in destination.rglob("*"):
                if item.exists():
                    item.chmod(stat.S_IRWXU)
            destination.chmod(stat.S_IRWXU)
            shutil.rmtree(destination)
        staging = self.root / f".tmp-{key}-{uuid.uuid4().hex}"
        copy_tree_independent(formal_run, staging)
        manifest = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "cache_key": key,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "versions": versions,
            "artifacts": entry_artifacts(staging),
        }
        (staging / "cache_entry.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        atomic_publish_directory(staging, destination)
        make_read_only(destination)
        return destination

    def materialise(self, key: str, staging: Path) -> str:
        """Verify and independently materialise an entry into formal-run staging."""
        source = self.path(key)
        valid, reason = verify_entry(source)
        if not valid:
            raise ValueError(f"Cache entry is unavailable or corrupt: {reason}")
        return copy_tree_independent(source, staging)

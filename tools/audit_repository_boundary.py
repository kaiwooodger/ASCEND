"""Fail CI when the Git snapshot contains runtime, clinical, secret, or oversized artifacts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 10 * 1024 * 1024
FORBIDDEN_PARTS = {
    "runs", "test_runs", "ASCEND_CASE", "clinical_data", "patient_data",
    "cohort_data", "retrospective_data", "screenshots",
}
FORBIDDEN_SUFFIXES = {
    ".dcm", ".dicom", ".npy", ".npz", ".nii", ".mha", ".mhd", ".nrrd",
    ".vti", ".vtp", ".stl", ".ply", ".glb", ".3mf", ".ips", ".p12", ".pfx",
}
TEXT_SECRET_PATTERN = re.compile(
    r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY|(?:api[_-]?key|password|secret)\s*[:=]\s*['\"][^'\"]+",
    re.IGNORECASE,
)
MACHINE_PATH_PATTERN = re.compile(r"/(?:Users|home)/[^/\s]+/")


def tracked_files() -> list[Path]:
    """Return paths in the exact Git snapshot inspected by Actions."""
    completed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True,
    )
    return [ROOT / value.decode() for value in completed.stdout.split(b"\0") if value]


def audit() -> list[str]:
    """Return deterministic publication-boundary violations."""
    violations: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & {part.lower() for part in FORBIDDEN_PARTS}:
            violations.append(f"forbidden runtime/clinical path: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name == "ascend_case.json":
            violations.append(f"forbidden medical/runtime artifact: {relative}")
        if path.stat().st_size > MAX_BYTES:
            violations.append(f"file exceeds {MAX_BYTES} bytes: {relative}")
        content = path.read_bytes()
        if len(content) >= 132 and content[128:132] == b"DICM":
            violations.append(f"DICOM preamble detected: {relative}")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if TEXT_SECRET_PATTERN.search(text):
            violations.append(f"possible embedded credential: {relative}")
        if MACHINE_PATH_PATTERN.search(text):
            violations.append(f"machine-specific home path: {relative}")
    return sorted(set(violations))


def main() -> int:
    """Print violations and return a CI-compatible status code."""
    violations = audit()
    if violations:
        print("Repository publication boundary failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print(f"Repository publication boundary passed: {len(tracked_files())} tracked files inspected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

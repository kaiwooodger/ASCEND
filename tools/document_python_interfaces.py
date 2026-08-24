"""Add deterministic baseline documentation to unlocked ASCEND Python interfaces.

This maintenance utility documents modules and public interfaces that do not yet
have docstrings. It deliberately excludes ``ascend/scientific/legacy`` because
those files are hash-locked scientific sources. The generated text describes
contracts and intent; it does not change executable statements.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "ascend"
LOCKED_FRAGMENT = "ascend/scientific/legacy"


MODULE_DESCRIPTIONS = {
    "__main__": "Package entry point that forwards command-line execution to the ASCEND CLI.",
    "controller": "Application orchestration for case import, configuration, dependency invalidation, analysis, and export.",
    "state": "Thread-safe application activity state shared by workstation adapters.",
    "cli": "Command-line interface for ASCEND ingestion, analysis, validation, cache, and export workflows.",
    "discovery": "Header-only DICOM discovery and inventory generation without scientific calculation.",
    "geometry": "Strict DICOM geometry validation and RTDOSE frame-offset normalization.",
    "relationships": "UID-based resolution and audited selection of DICOM-RT treatment chains.",
    "roi": "RTSTRUCT ROI identity, lookup, validation, and inventory helpers.",
    "rtplan_config": "Auditable RTPLAN/RTDOSE configuration extraction with ambiguity-preserving prefilling.",
    "layer22_viewer": "Qt 3D and orthogonal-slice presentation of stored Layer 2.2 geometry and dose evidence.",
    "main_window": "Native Qt Widgets workstation that presents controller state without performing scientific calculations.",
    "theme": "Shared Qt visual system and normalized workstation status presentation.",
    "artifacts": "Deterministic Layer 1 artifact serialization and hashing helpers.",
    "cache": "Immutable case-local Layer 1 cache publication, verification, and materialization.",
    "incremental_raster": "Incremental mask rasterization adapter that limits peak memory while preserving locked behavior.",
    "selection": "Identity-based ROI selection, filtering, inventory, and effective-role derivation.",
    "service": "Service-layer orchestration for the enclosing ASCEND package.",
    "basis": "Construction and caching of the validated voxelwise Layer 3.1 P/Q biological basis.",
    "biological_metrics": "Biological counterparts and contextual mappings of stored Layer 2.1 metrics.",
    "metrics": "Pure conventional-LQ BED/EQD2 transformations and ROI summary functions.",
    "models": "Typed records for treatment, case, biological, or validation state.",
    "parameters": "Validation and parsing of Layer 3.1 tissue parameters and sensitivity sweeps.",
    "reporting": "Deterministic rendering of stored ASCEND results into human-readable artifacts.",
    "validation": "Independent computational verification helpers for the enclosing analysis layer.",
    "placeholders": "Explicit non-implemented interfaces reserved for future biological modelling layers.",
    "case": "Persistent ASCEND case and layer-run records with relocation-safe serialization.",
    "config": "Validated user and DICOM-derived configuration contracts for an ASCEND case.",
    "status": "Canonical calculation, interpretation, applicability, and severity states.",
    "export": "Case-level JSON and CSV export adapters over already stored results.",
    "applicability": "Treatment-context rules that separate calculability from clinical interpretability.",
    "analytic_dose": "Analytic physical-dose fields used for independent anisotropic-grid verification.",
    "analytic_geometry": "Physical-coordinate analytic shapes used for anisotropic-grid verification.",
    "comparison": "Independent comparison calculations for validation evidence.",
    "fixtures": "Deterministic synthetic fixtures used by independent validation workstreams.",
    "eclipse_dvh": "Parsing, normalization, and Layer 1 comparison of Eclipse DVH text exports.",
    "matching": "Identity-first matching of Eclipse reference structures to ASCEND results.",
    "reference_import": "Strict import of canonical CSV and Eclipse text reference endpoints.",
    "schemas": "Versioned records and acceptance criteria for formal Eclipse comparison.",
    "statistics": "Aggregate validation statistics and Bland–Altman summaries.",
    "layer31": "Analytic Layer 3.1 computational verification and performance benchmarking.",
    "provenance": "Canonical hashing, run identifiers, and shared provenance records.",
    "geometry": "Geometry calculations used by DICOM ingestion or validation diagnostics.",
    "server": "Optional localhost HTTP adapter over the ASCEND application controller.",
    "preferences": "Presentation and workflow preferences that do not alter locked scientific formulas.",
}


def words(name: str) -> str:
    """Convert a Python identifier into readable lower-case words."""
    separated = re.sub(r"(?<!^)(?=[A-Z])", " ", name).replace("_", " ")
    return " ".join(separated.lower().split())


def module_doc(path: Path) -> str:
    """Return a concise module contract based on its functional package and filename."""
    stem = path.stem
    if stem == "__init__":
        package = ".".join(path.relative_to(PROJECT_ROOT).parent.parts)
        return f'"""Public package interface for ``{package}``."""'
    description = MODULE_DESCRIPTIONS.get(stem, f"ASCEND {words(stem)} implementation.")
    return f'"""{description}"""'


def interface_doc(node: ast.AST, owner: str | None = None) -> str:
    """Generate a contract-oriented docstring for one public class or callable."""
    name = getattr(node, "name")
    readable = words(name)
    if isinstance(node, ast.ClassDef):
        if any(isinstance(base, ast.Name) and base.id == "Enum" for base in node.bases):
            return f"Enumerate supported {readable} values."
        if name.endswith("Error") or name.endswith("Scope"):
            return f"Signal a controlled {readable} condition."
        if name.endswith("Service"):
            return f"Coordinate the {readable.removesuffix(' service')} workflow without GUI-side calculation."
        if name.endswith("Tests"):
            return f"Verify {readable.removesuffix(' tests')} behavior."
        return f"Represent {readable} state and behavior."
    if name == "to_dict":
        return "Return a JSON-serializable representation of this record."
    if name.startswith("from_"):
        return f"Construct this record from {words(name[5:])}."
    if name == "validate" or name.startswith("validate_"):
        subject = readable.removeprefix("validate ")
        return f"Validate {subject} and raise a controlled error when requirements are not met."
    if name.startswith("build_") or name == "build":
        return f"Build {readable.removeprefix('build ')} from validated inputs."
    if name.startswith("write_") or name == "write":
        return f"Write {readable.removeprefix('write ')} deterministically to disk."
    if name.startswith("parse_"):
        return f"Parse {readable.removeprefix('parse ')} using the documented input contract."
    if name.startswith("export"):
        return f"Export {readable.removeprefix('export ')} from stored results without recalculation."
    if name.startswith("run") or name == "run":
        subject = readable.removeprefix("run ") or words(owner or "workflow")
        return f"Execute {subject} and return its explicit calculation state and evidence."
    if name.startswith("set_"):
        return f"Update {readable.removeprefix('set ')} presentation state."
    if name.startswith("select_"):
        return f"Select {readable.removeprefix('select ')} using explicit deterministic criteria."
    if name.startswith("clear"):
        return f"Clear {readable.removeprefix('clear ')} only after the caller's authorization requirements are met."
    if name.startswith("load"):
        return f"Load {readable.removeprefix('load ')} and verify its expected contract."
    if name.startswith("normalise"):
        return f"Normalize {readable.removeprefix('normalise ')} without changing scientific meaning."
    if name.startswith("compare"):
        return f"Compare {readable.removeprefix('compare ')} and retain auditable evidence."
    if name.startswith("resolve"):
        return f"Resolve {readable.removeprefix('resolve ')} without silently guessing ambiguous meaning."
    if name.startswith("inspect"):
        return f"Inspect {readable.removeprefix('inspect ')} without mutating stored state."
    if name.startswith("calculate") or name.startswith("analyse"):
        return f"Calculate {readable.split(' ', 1)[-1]} using the documented validated inputs."
    return f"Handle {readable} for the enclosing ASCEND workflow."


def public_nodes(tree: ast.Module) -> list[tuple[ast.AST, str | None]]:
    """Return top-level public interfaces and public methods in source order."""
    found: list[tuple[ast.AST, str | None]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            found.append((node, None))
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                        found.append((child, node.name))
    return found


def document_file(path: Path) -> bool:
    """Insert missing module and public-interface docstrings without changing code statements."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    insertions: list[tuple[int, str]] = []
    if ast.get_docstring(tree) is None:
        insertions.append((0, module_doc(path) + "\n\n"))
    for node, owner in public_nodes(tree):
        if ast.get_docstring(node) is not None or not getattr(node, "body", None):
            continue
        # A one-line definition such as ``def run(): ...`` has no safe
        # indentation boundary for inserting a docstring without rewriting
        # executable syntax. These rare interfaces are documented manually.
        if getattr(node, "lineno", None) == getattr(node, "end_lineno", None):
            continue
        body_line = node.body[0].lineno - 1
        indentation = " " * node.body[0].col_offset
        insertions.append((body_line, f'{indentation}"""{interface_doc(node, owner)}"""\n'))
    if not insertions:
        return False
    for index, text in sorted(insertions, reverse=True):
        lines.insert(index, text)
    path.write_text("".join(lines), encoding="utf-8")
    return True


def main() -> int:
    """Document every unlocked ASCEND Python module and report the changed-file count."""
    changed = 0
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if LOCKED_FRAGMENT in path.as_posix():
            continue
        changed += int(document_file(path))
    print(f"Documented {changed} unlocked ASCEND Python files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Persistent ASCEND case and layer-run records with relocation-safe serialization."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import CaseConfiguration
from .status import CalculationStatus, InterpretationStatus, Layer1Status


@dataclass
class LayerRun:
    """Represent layer run state and behavior."""
    layer: str
    calculation_status: str = CalculationStatus.NOT_RUN.value
    interpretation_status: str = InterpretationStatus.NOT_INTERPRETABLE.value
    run_id: str | None = None
    parent_layer1_run_id: str | None = None
    result_path: str | None = None
    result: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    stale_reason: str | None = None

    def mark_stale(self, reason: str) -> None:
        """Handle mark stale for the enclosing ASCEND workflow."""
        if self.calculation_status not in {
            CalculationStatus.NOT_RUN.value, CalculationStatus.NOT_IMPLEMENTED.value,
        }:
            self.calculation_status = CalculationStatus.STALE.value
            self.stale_reason = reason


@dataclass
class ASCENDCase:
    """Represent a s c e n d case state and behavior."""
    case_root: str
    case_id: str = "unidentified"
    patient_metadata: dict[str, Any] = field(default_factory=dict)
    study_metadata: dict[str, Any] = field(default_factory=dict)
    dicom_objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    dicom_chains: list[dict[str, Any]] = field(default_factory=list)
    selected_chain_id: str | None = None
    chain_selection: dict[str, Any] = field(default_factory=dict)
    selected_objects: dict[str, str | list[str] | None] = field(default_factory=dict)
    configuration: CaseConfiguration = field(default_factory=CaseConfiguration)
    effective_structure_roles: dict[str, str | list[str]] = field(default_factory=dict)
    layer1_status: str = Layer1Status.NOT_RUN.value
    layer1: LayerRun = field(default_factory=lambda: LayerRun("layer1"))
    layer2_1: LayerRun = field(default_factory=lambda: LayerRun("layer2_1"))
    layer2_2: LayerRun = field(default_factory=lambda: LayerRun("layer2_2"))
    layer3_1: LayerRun = field(default_factory=lambda: LayerRun("layer3_1"))
    layer3_2: LayerRun = field(default_factory=lambda: LayerRun("layer3_2"))
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    configuration_hash: str | None = None

    @property
    def root(self) -> Path:
        """Handle root for the enclosing ASCEND workflow."""
        return Path(self.case_root)

    def initialise_directories(self) -> None:
        """Handle initialise directories for the enclosing ASCEND workflow."""
        for path in (
            self.root / "raw", self.root / "validated",
            self.root / "derived" / "layer2_1", self.root / "derived" / "layer2_2",
            self.root / "derived" / "layer3_1", self.root / "derived" / "layer3_2",
            self.root / "exports", self.root / "logs",
            self.root / "cache" / "layer1",
            self.root / "cache" / "layer3_1", self.root / "cache" / "layer3_2",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def to_dict(self, include_results: bool = True) -> dict[str, Any]:
        """Return a JSON-serializable representation of this record."""
        data = asdict(self)
        if not include_results:
            for layer in ("layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"):
                data[layer]["result"] = None
        return data

    def save(self) -> Path:
        """Handle save for the enclosing ASCEND workflow."""
        self.initialise_directories()
        path = self.root / "ascend_case.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "ASCENDCase":
        """Load load and verify its expected contract."""
        source = Path(path).resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        saved_root = Path(raw.get("case_root", source.parent))
        current_root = source.parent
        if current_root != saved_root and not (saved_root / "ascend_case.json").is_file():
            old_prefix = str(saved_root)
            new_prefix = str(current_root)

            def relocate(value: Any) -> Any:
                if isinstance(value, str) and (value == old_prefix or value.startswith(old_prefix + "/")):
                    return new_prefix + value[len(old_prefix):]
                if isinstance(value, list):
                    return [relocate(item) for item in value]
                if isinstance(value, dict):
                    return {key: relocate(item) for key, item in value.items()}
                return value

            raw = relocate(raw)
            raw["case_root"] = new_prefix
        raw["configuration"] = CaseConfiguration.from_dict(raw.get("configuration", {}))
        for key in ("layer1", "layer2_1", "layer2_2", "layer3_1", "layer3_2"):
            raw[key] = LayerRun(**raw[key])
        # ASCEND 1.3.2 persisted intentional blank-canvas placeholders. They
        # are workflow state, not scientific evidence, and migrate to NOT RUN
        # now that the 1.4.1 services are installed.
        for key in ("layer3_1", "layer3_2"):
            record = raw[key]
            if record.calculation_status == CalculationStatus.NOT_IMPLEMENTED.value and record.result is None:
                raw[key] = LayerRun(key)
        return cls(**raw)

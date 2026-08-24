"""Read-only 2D/3D presentation of stored Layer 3.1 biological fields."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
import hashlib
from pathlib import Path
import shutil
import sys
from typing import Any

import numpy as np
from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DExtras import Qt3DExtras
from PySide6.Qt3DRender import Qt3DRender
from PySide6.QtCore import QByteArray, QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QTransform, QVector3D
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSlider, QTabWidget, QVBoxLayout, QWidget, QLineEdit,
)
from scipy import ndimage

from ascend.gui.layer22_viewer import _material
from ascend.layer3.visualization import BiologicalMeshResult, build_biological_mesh
from ascend.layer3.spatial_biology import (
    BiologyColorScaleController, BiologyViewerState, SpatialBiologyField,
    voxel_spacing_zyx_mm, voxel_to_world_lps, world_to_voxel_lps,
)
from ascend.models.case import ASCENDCase
from ascend.scientific.legacy import layer21_validated as handoff
from ascend.validation.provenance import file_hash


@dataclass
class Layer31ViewerData:
    fields: dict[str, np.ndarray]
    field_metadata: dict[str, dict[str, Any]]
    masks: dict[str, np.ndarray]
    geometry: dict[str, Any]
    result: dict[str, Any]
    case_root: Path
    spatial_fields: dict[str, SpatialBiologyField] = dataclass_field(default_factory=dict)
    vertex_centres_lps_mm: dict[str, tuple[float, float, float]] = dataclass_field(default_factory=dict)
    graph_edges_lps_mm: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = ()


@dataclass
class CADSceneBundle:
    """Display-only collection of anatomical and biological CAD surfaces."""
    anatomy_meshes: dict[str, BiologicalMeshResult]
    overlay_mesh: BiologicalMeshResult | None
    overlay_target: str | None
    overlay_field_id: str | None
    overlay_label: str | None
    overlay_units: str | None
    smoothing_enabled: bool
    failures: dict[str, str]
    mode: str = "SURFACE"
    special_meshes: dict[str, BiologicalMeshResult] = dataclass_field(default_factory=dict)
    show_contours: bool = False
    gtv_opacity: float = 0.96
    oar_opacity: float = 0.25
    isosurface_opacity: float = 0.45
    vertex_centres_lps_mm: tuple[tuple[float, float, float], ...] = ()
    graph_edges_lps_mm: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...] = ()


def _build_cad_scene_bundle(
    data: Layer31ViewerData,
    anatomy_names: tuple[str, ...],
    overlay_field_id: str | None,
    smoothing: dict[str, Any] | None,
    scalar_range: tuple[float, float] | None,
    *,
    display_mode: str = "SURFACE",
    cut_axis: str = "axial",
    cut_fraction: float = 0.5,
    cut_inverted: bool = False,
    cut_azimuth_degrees: float = 0.0,
    cut_elevation_degrees: float = 0.0,
    isosurface_thresholds: tuple[float, ...] = (),
    show_contours: bool = False,
    gtv_opacity: float = 0.96,
    oar_opacity: float = 0.25,
    isosurface_opacity: float = 0.45,
    show_vertex_centres: bool = False,
    show_graph: bool = False,
) -> CADSceneBundle:
    """Build CAD surfaces outside the GUI thread without scientific recalculation."""
    anatomy: dict[str, BiologicalMeshResult] = {}
    failures: dict[str, str] = {}
    physical = data.fields["physical_course_dose_gy"]
    for name in anatomy_names:
        mask = np.asarray(data.masks[name], dtype=bool)
        if not mask.any():
            failures[name] = "empty_validated_mask"
            continue
        result = build_biological_mesh(
            data.case_root, f"anatomy::{name}", mask, physical, data.geometry,
            "physical_course_dose_gy", "Gy", smoothing, None,
            export_formats=("stl",), expected_tissue_mask=mask,
        )
        if result.status == "PASS":
            anatomy[name] = result
        else:
            failures[name] = str(result.reason or "surface_unavailable")
    overlay_target = "Region: Whole GTV" if (
        "Region: Whole GTV" in data.masks and np.asarray(data.masks["Region: Whole GTV"]).any()
    ) else next((name for name in anatomy_names if not name.startswith("OAR:")), None)
    overlay: BiologicalMeshResult | None = None
    special: dict[str, BiologicalMeshResult] = {}
    if overlay_field_id and overlay_target and overlay_target in data.masks:
        meta = data.field_metadata[overlay_field_id]
        gtv_mask = np.asarray(data.masks[overlay_target], dtype=bool)
        mode = display_mode.upper()
        if mode == "CUTAWAY":
            axis = {"axial": 0, "coronal": 1, "sagittal": 2}.get(cut_axis, 0)
            normal = np.zeros(3, dtype=float); normal[axis] = 1.0
            azimuth = np.deg2rad(float(cut_azimuth_degrees)); elevation = np.deg2rad(float(cut_elevation_degrees))
            rotate_z = np.asarray([[np.cos(azimuth), -np.sin(azimuth), 0.0], [np.sin(azimuth), np.cos(azimuth), 0.0], [0.0, 0.0, 1.0]])
            rotate_y = np.asarray([[np.cos(elevation), 0.0, np.sin(elevation)], [0.0, 1.0, 0.0], [-np.sin(elevation), 0.0, np.cos(elevation)]])
            normal = rotate_z @ rotate_y @ normal
            coordinates = np.indices(gtv_mask.shape, dtype=float)
            projection = np.tensordot(normal, coordinates, axes=(0, 0))
            inside_projection = projection[gtv_mask]
            threshold = float(np.quantile(inside_projection, np.clip(float(cut_fraction), 0.0, 1.0)))
            retained = projection >= threshold if cut_inverted else projection <= threshold
            display_mask = gtv_mask & retained
            roi_id = (
                f"biological-cutaway::{overlay_target}::{cut_axis}::{threshold:.6g}::"
                f"{cut_azimuth_degrees:.3g}::{cut_elevation_degrees:.3g}::{int(cut_inverted)}"
            )
        else:
            display_mask = gtv_mask
            roi_id = f"biological-overlay::{overlay_target}"
        if mode != "ISOSURFACE":
            overlay = build_biological_mesh(
                data.case_root, roi_id, display_mask,
                data.fields[overlay_field_id], data.geometry, overlay_field_id,
                str(meta["units"]), smoothing, scalar_range, export_formats=("vtp", "stl"),
                expected_tissue_mask=gtv_mask,
            )
            if overlay.status != "PASS":
                failures[f"overlay::{overlay_target}"] = str(overlay.reason or "surface_unavailable")
                overlay = None
        if mode == "ISOSURFACE":
            for threshold in isosurface_thresholds[:4]:
                threshold_mask = gtv_mask & np.isfinite(data.fields[overlay_field_id]) & (data.fields[overlay_field_id] >= float(threshold))
                if not threshold_mask.any() or np.array_equal(threshold_mask, gtv_mask):
                    failures[f"isosurface::{threshold:g}"] = "threshold_region_empty_or_whole_roi"
                    continue
                item = build_biological_mesh(
                    data.case_root, f"biological-isosurface::{overlay_target}::{threshold:.8g}", threshold_mask,
                    data.fields[overlay_field_id], data.geometry, overlay_field_id,
                    str(meta["units"]), smoothing, scalar_range, export_formats=("vtp", "stl"),
                    expected_tissue_mask=gtv_mask,
                )
                if item.status == "PASS": special[f"≥ {threshold:g} {meta['units']}"] = item
                else: failures[f"isosurface::{threshold:g}"] = str(item.reason or "surface_unavailable")
    meta = data.field_metadata.get(overlay_field_id or "", {})
    return CADSceneBundle(
        anatomy, overlay, overlay_target, overlay_field_id,
        str(meta.get("label")) if meta else None,
        str(meta.get("units")) if meta else None,
        bool((smoothing or {}).get("method", "taubin_non_shrinking") != "none"),
        failures,
        display_mode.upper(), special, bool(show_contours),
        float(np.clip(gtv_opacity, 0.0, 1.0)), float(np.clip(oar_opacity, 0.0, 1.0)),
        float(np.clip(isosurface_opacity, 0.0, 1.0)),
        tuple(data.vertex_centres_lps_mm.values()) if show_vertex_centres else (),
        data.graph_edges_lps_mm if show_graph else (),
    )


def prepare_layer31_viewer_data(case: ASCENDCase) -> Layer31ViewerData:
    """Load hash-verified authoritative fields and validated native masks."""
    # Large fields are intentionally absent from summary-only runs.  Their
    # scientific service materialises and hash-publishes them when requested.
    from ascend.layer3.lq.service import Layer31Service
    Layer31Service().materialise_visualisation_fields(case)
    result = case.layer3_1.result or {}
    if case.layer3_1.calculation_status not in {"completed", "completed_with_warnings"}:
        raise ValueError("A current completed Layer 3.1 run is required.")
    layer31a = result.get("layer3_1a_conventional_lq") or {}
    artifact = layer31a.get("artifacts") or {}
    path = Path(str(artifact.get("spatial_fields_path") or ""))
    if not path.is_file() or file_hash(path) != artifact.get("spatial_fields_sha256"):
        raise ValueError("Layer 3.1 spatial field archive is missing or its hash differs.")
    fields: dict[str, np.ndarray] = {}
    metadata: dict[str, dict[str, Any]] = {}
    with np.load(path, allow_pickle=False) as archive:
        physical_reference = artifact.get("physical_course_dose_reference") or {}
        basis_directory = Path(str(physical_reference.get("basis_cache_path") or ""))
        basis_archive = basis_directory / "pq_basis.npz"
        if not basis_archive.is_file():
            raise ValueError("Layer 3.1 physical-dose P reference is missing.")
        with np.load(basis_archive, allow_pickle=False) as basis_data:
            fields["physical_course_dose_gy"] = np.asarray(basis_data["P_gy"], dtype=np.float32)
        physical_hash = hashlib.sha256(
            np.ascontiguousarray(fields["physical_course_dose_gy"]).view(np.uint8)
        ).hexdigest()
        if physical_hash != physical_reference.get("array_sha256"):
            raise ValueError("Layer 3.1 physical-dose P reference hash differs.")
        metadata["physical_course_dose_gy"] = {
            "label": "Physical absorbed dose", "units": "Gy", "alpha_beta_gy": None,
            "palette": "physical_dose",
            "category": "Physical input", "equation": "D(x)",
            "interpretation": "Delivered physical dose on the validated RTDOSE grid. This is the input to the biological transformations, not a biological outcome.",
        }
        if "LQ_high_dose_warning_mask" in archive.files:
            fields["LQ_high_dose_warning_mask"] = np.asarray(archive["LQ_high_dose_warning_mask"], dtype=np.float32)
            metadata["LQ_high_dose_warning_mask"] = {
            "label": "LQ model-domain warning", "units": "binary", "alpha_beta_gy": None,
            "palette": "warning",
            "category": "Model-domain warning", "equation": "1[max fraction dose ≥ configured criterion]",
            "interpretation": "Highlighted voxels exceed the configured operational LQ-warning criterion. The LQ BED/EQD2 calculation is retained; this does not switch voxels to MLQ and is not a toxicity flag.",
            }
        for record in layer31a.get("spatial_fields", []):
            alpha_beta = record.get("alpha_beta_gy")
            for kind, units in (("BED", "Gy BED"), ("EQD2", "Gy EQD2")):
                key = record.get(f"spatial_{kind}_LQ_array_key")
                if key and key in archive.files:
                    fields[str(key)] = np.asarray(archive[str(key)], dtype=np.float32)
                    compact_units = f"Gy{alpha_beta:g}"
                    metadata[str(key)] = {
                        "label": f"Spatial {kind} — LQ reference · α/β {alpha_beta:g} Gy",
                        "units": compact_units,
                        "palette": "biological_lq",
                        "alpha_beta_gy": alpha_beta,
                        "bound_roi_identities": record.get("bound_roi_identities", []),
                        "category": "3.1A conventional LQ reference",
                        "equation": "BED(x) = Σf dƒ(x)[1+dƒ(x)/(α/β)]" if kind == "BED" else "EQD2(x) = BED(x) / [1+2/(α/β)]",
                        "interpretation": (
                            "Voxelwise conventional-LQ biologically effective dose. It is a model-derived reference quantity, not physical dose or a clinical outcome."
                            if kind == "BED" else
                            "Voxelwise conventional-LQ dose expressed as a 2 Gy/fraction equivalent. It is a model-derived reference quantity, not delivered dose."
                        ),
                    }
    layer31b = result.get("layer3_1b_high_dose_sfrt_response") or {}
    survival_artifact = layer31b.get("artifacts") or {}
    survival_path = Path(str(survival_artifact.get("survival_fields_path") or ""))
    if survival_path.is_file() and file_hash(survival_path) == survival_artifact.get("survival_fields_sha256"):
        with np.load(survival_path, allow_pickle=False) as archive:
            fields["voxel_survival_MLQ"] = np.asarray(archive["voxel_survival_MLQ"], dtype=np.float32)
            metadata["voxel_survival_MLQ"] = {
                "label": "MLQ model-derived surviving fraction", "units": "fraction", "alpha_beta_gy": None,
                "palette": "survival",
                "category": "3.1B tumour response", "equation": "SF(x) = exp[-Σf Kƒ(x)]",
                "interpretation": "Modelled direct surviving fraction after the declared fraction events. Lower values represent stronger modelled direct effect. This is not TCP.",
            }
            # This is a display transform of the stored authoritative survival
            # field. It improves contrast without becoming a new scientific
            # result or changing any stored endpoint.
            fields["negative_log10_survival_MLQ"] = -np.log10(
                np.clip(fields["voxel_survival_MLQ"], np.finfo(np.float32).tiny, 1.0)
            ).astype(np.float32)
            metadata["negative_log10_survival_MLQ"] = {
                "label": "MLQ survival contrast · −log₁₀(SF)", "units": "log10 survival reduction", "alpha_beta_gy": None,
                "palette": "survival",
                "category": "3.1B tumour response · display transform", "equation": "−log₁₀[SF_MLQ(x)]",
                "interpretation": "Higher values mean lower model-predicted surviving fraction. Values are transformed for display only; numerical summaries retain SF.",
            }
            fields["course_effect_MLQ"] = -np.log(
                np.clip(fields["voxel_survival_MLQ"], np.finfo(np.float32).tiny, 1.0)
            ).astype(np.float32)
            metadata["course_effect_MLQ"] = {
                "label": "Accumulated MLQ effect", "units": "dimensionless effect", "alpha_beta_gy": None,
                "palette": "effect",
                "category": "3.1B tumour response", "equation": "K(x) = Σf [αdƒ+βG(xƒ)dƒ²]",
                "interpretation": "Accumulated model exponent before conversion to survival. Larger values produce lower modelled surviving fraction; this is not probability of control.",
            }
    layer31d = result.get("layer3_1d_tumour_control_probability") or {}
    tcp_artifact = layer31d.get("artifacts") or {}
    tcp_path = Path(str(tcp_artifact.get("tcp_fields_path") or ""))
    if tcp_path.is_file() and file_hash(tcp_path) == tcp_artifact.get("tcp_fields_sha256"):
        with np.load(tcp_path, allow_pickle=False) as archive:
            tcp_field_contracts = {
                "residual_clonogen_field_radiation_only": (
                    "Residual clonogen burden — radiation only", "clonogens/voxel",
                    "Expected residual clonogenic burden assigned to each tumour voxel under direct MLQ radiation survival. This is not a risk map."
                ),
                "residual_clonogen_field_repopulation_corrected": (
                    "Residual clonogen burden — repopulation corrected", "clonogens/voxel",
                    "Expected residual clonogenic burden after the separately configured delayed-repopulation multiplier. This is not a clinical outcome map."
                ),
                "residual_clonogen_density_repopulation_corrected": (
                    "Residual clonogen density — repopulation corrected", "clonogens/cm3",
                    "Modelled residual clonogen density. It is distinct from per-voxel burden and is biologically unvalidated."
                ),
                "net_clonogenic_multiplier": (
                    "Net clonogenic multiplier", "multiplier",
                    "Radiation survival multiplied by the configured repopulation factor. It is not a survival probability and may exceed one."
                ),
                "residual_clonogen_fraction_of_total": (
                    "Fraction of total residual clonogen burden", "fraction/voxel",
                    "Each voxel's fraction of the complete modelled residual burden; values sum to one over the tumour when residual burden is non-zero."
                ),
                "log10_residual_clonogen_burden": (
                    "Log10 residual clonogen burden", "log10 clonogens/voxel",
                    "Logarithmic display of per-voxel residual burden for dynamic-range management. Underlying aggregation remains linear and unsmoothed."
                ),
            }
            for key, (label, units, interpretation) in tcp_field_contracts.items():
                if key not in archive.files:
                    continue
                fields[key] = np.asarray(archive[key], dtype=np.float32)
                metadata[key] = {
                    "label": label, "units": units, "alpha_beta_gy": None,
                    "palette": "tcp_residual", "category": "3.1D direct-clonogenic TCP",
                    "equation": "mu_i = rho V_i exp(-Psi_i + Phi_rep)" if "residual" in key else "M_i = exp(-Psi_i + Phi_rep)",
                    "interpretation": interpretation,
                }
    if not case.layer1.result_path:
        raise ValueError("Validated Layer 1 masks are unavailable.")
    _record, _dose, stored_masks = handoff.load_handoff(Path(case.layer1.result_path).parent)
    masks: dict[str, np.ndarray] = {}
    inventory = (case.layer1.result or {}).get("manifest", {}).get("roi_inventory", [])
    by_identity = {
        (str(item.get("roi_identity", {}).get("rtstruct_sop_instance_uid", "")), int(item.get("roi_identity", {}).get("roi_number", -1))): item
        for item in inventory if item.get("roi_identity") and item.get("rasterisation_status") == "rasterised"
    }
    role_masks: dict[str, np.ndarray] = {}
    for role, configured in case.effective_structure_roles.items():
        names = configured if isinstance(configured, list) else [configured]
        selected: list[np.ndarray] = []
        for name in names:
            if isinstance(name, str) and name in stored_masks:
                label = f"{role}: {name}"
                value = np.asarray(stored_masks[name], dtype=bool)
                masks[label] = value; selected.append(value)
        if selected:
            role_masks[role] = np.logical_or.reduce(selected)
    for item in case.configuration.oar_structures:
        identity = item.get("roi_identity") or {}
        inventory_item = by_identity.get((str(identity.get("rtstruct_sop_instance_uid", "")), int(identity.get("roi_number", -1))))
        name = item.get("canonical_mapping") or (inventory_item or {}).get("canonical_mapping") or item.get("name")
        if isinstance(name, str) and name in stored_masks:
            masks[f"OAR: {item.get('display_name') or item.get('name') or name}"] = np.asarray(stored_masks[name], dtype=bool)
    for assignment in case.configuration.layer31_roi_parameters:
        identity = assignment.get("roi_identity") or {}
        inventory_item = by_identity.get((str(identity.get("rtstruct_sop_instance_uid", "")), int(identity.get("roi_number", -1))))
        name = (inventory_item or {}).get("canonical_mapping")
        if isinstance(name, str) and name in stored_masks:
            label = f"Tissue: {(inventory_item or {}).get('original_name') or assignment.get('roi_name') or name}"
            masks[label] = np.asarray(stored_masks[name], dtype=bool)
    if not masks:
        raise ValueError("No validated Layer 1 masks are available to the Layer 3.1 viewer.")
    # Add deterministic tumour-region focus masks for interactive explanation.
    # These are presentation masks derived only from validated Layer 1 masks;
    # they do not alter Layer 3.1 calculations or stored regional endpoints.
    gtv = role_masks.get("GTV")
    if gtv is not None:
        high = role_masks.get("VTV_H", np.zeros_like(gtv)) & gtv
        valley = role_masks.get("VTV_L", np.zeros_like(gtv)) & gtv & ~high
        other = gtv & ~high & ~valley
        masks["Region: Whole GTV"] = gtv
        masks["Region: Vertices"] = high
        masks["Region: Valleys"] = valley
        masks["Region: Other GTV"] = other
    shape = next(iter(fields.values())).shape
    if any(value.shape != shape for value in fields.values()) or any(value.shape != shape for value in masks.values()):
        raise ValueError("Layer 3.1 viewer fields and masks do not share one validated geometry.")
    geometry = dict(layer31a.get("geometry") or {})
    geometry["spacing"] = geometry.get("spacing", geometry.get("in_plane_spacing_mm"))
    for key, values in fields.items():
        array = np.asarray(values)
        metadata[key]["display_range"] = (
            (float(np.nanmin(array)), float(np.nanmax(array))) if np.isfinite(array).any() else (0.0, 1.0)
        )
    # Formal downstream field contracts.  They contain no calculation logic;
    # they bind stored authoritative arrays to the one validated LPS geometry.
    spacing_zyx = voxel_spacing_zyx_mm(geometry)
    normal = np.asarray(geometry["normal"], dtype=float)
    offsets = np.asarray(geometry["offsets"], dtype=float)
    direction_sign = -1.0 if len(offsets) > 1 and offsets[-1] < offsets[0] else 1.0
    origin = np.asarray(geometry["origin"], dtype=float) + normal * float(offsets[0])
    direction = (
        tuple((normal * direction_sign).tolist()),
        tuple(np.asarray(geometry.get("column_direction", geometry.get("col_dir")), dtype=float).tolist()),
        tuple(np.asarray(geometry.get("row_direction", geometry.get("row_dir")), dtype=float).tolist()),
    )
    tissue_labels = np.zeros(shape, dtype=np.uint16)
    for label, name in enumerate(
        [item for item in ("Region: Whole GTV", *[key for key in masks if key.startswith("OAR:")]) if item in masks],
        start=1,
    ):
        tissue_labels[np.asarray(masks[name], dtype=bool)] = label
    model_name = str(layer31a.get("formalism_id") or "LQ")
    history = result.get("fraction_history") or layer31a.get("fraction_history") or {}
    components = tuple(history.get("components") or (result.get("treatment_context") or {}).get("components") or ())
    source_uids = tuple(str(item) for item in (layer31a.get("dose_uids") or layer31a.get("source_dose_uids") or ()))
    contracts: dict[str, SpatialBiologyField] = {}
    for key, values in fields.items():
        if key == "physical_course_dose_gy": quantity = "PHYSICAL_DOSE"
        elif "EQD2" in key: quantity = "S_EQD2"
        elif "BED" in key: quantity = "S_BED"
        else: continue
        meta = metadata[key]
        contracts[key] = SpatialBiologyField(
            values_3d=np.asarray(values), quantity=quantity, units=str(meta["units"]),
            origin_lps_mm=tuple(origin.tolist()), spacing_mm=tuple(spacing_zyx.tolist()),
            direction_matrix=direction, shape=tuple(shape), valid_mask=np.isfinite(values),
            tissue_label_map=tissue_labels, roi_masks=masks, model_name=model_name,
            alpha_beta_metadata={"alpha_beta_gy": meta.get("alpha_beta_gy"), "bound_roi_identities": meta.get("bound_roi_identities", [])},
            fractionation_metadata=history, treatment_components=components,
            source_dose_uids=source_uids, field_id=key,
        )
    graph_nodes: dict[str, tuple[float, float, float]] = {}
    graph_edges: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    layer22 = case.layer2_2.result or {}
    if case.layer2_2.calculation_status in {"completed", "completed_with_warnings"}:
        for node in layer22.get("nodes", []):
            point = np.asarray(node.get("centroid_lps_mm"), dtype=float)
            if point.shape == (3,) and np.isfinite(point).all():
                graph_nodes[str(node.get("node"))] = tuple(map(float, point))
        for edge in layer22.get("edges", []):
            node_ids = edge.get("nodes") or []
            if len(node_ids) == 2 and str(node_ids[0]) in graph_nodes and str(node_ids[1]) in graph_nodes:
                graph_edges.append((graph_nodes[str(node_ids[0])], graph_nodes[str(node_ids[1])]))
    return Layer31ViewerData(fields, metadata, masks, geometry, result, case.root, contracts, graph_nodes, tuple(graph_edges))


def _slice(values: np.ndarray, orientation: str, index: int) -> np.ndarray:
    if orientation == "axial":
        return values[index]
    if orientation == "sagittal":
        return values[:, :, index]
    return values[:, index, :]


def _colour_map(values: np.ndarray, low: float, high: float, palette: str = "biological_lq") -> np.ndarray:
    fraction = np.zeros_like(values, dtype=float) if high <= low else np.clip((values - low) / (high - low), 0, 1)
    anchors_by_palette = {
        "physical_dose": [[8, 29, 88], [21, 101, 192], [23, 190, 207], [253, 210, 52], [211, 38, 49]],
        "survival": [[0, 0, 4], [68, 15, 118], [166, 54, 94], [238, 104, 60], [252, 253, 191]],
        "effect": [[8, 29, 88], [43, 97, 155], [30, 158, 137], [137, 213, 72], [255, 237, 77]],
        "warning": [[25, 25, 25], [255, 72, 60]],
        "biological_lq": [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]],
    }
    anchors = np.asarray(anchors_by_palette.get(palette, anchors_by_palette["biological_lq"]), dtype=float)
    position = fraction * (len(anchors) - 1)
    lower = np.floor(position).astype(int); upper = np.minimum(lower + 1, len(anchors) - 1)
    weight = (position - lower)[..., None]
    return np.round(anchors[lower] * (1 - weight) + anchors[upper] * weight).astype(np.uint8)


class BiologicalSliceCanvas(QWidget):
    voxelSelected = Signal(int, int, int)

    def __init__(self, orientation: str) -> None:
        super().__init__(); self.orientation = orientation; self.data: Layer31ViewerData | None = None
        self.field = ""; self.index = 0; self.roi = ""; self.show_structures = True; self.show_warning = True
        self.visible_rois: list[str] = []
        self.scalar_range: tuple[float, float] | None = None
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self._drag_position: QPointF | None = None
        self.crosshair: tuple[int, int, int] | None = None
        self._image_size: tuple[int, int] = (0, 0); self._display_size: tuple[int, int] = (0, 0); self._display_center = QPointF()
        self.setMinimumSize(280, 300)
        self.setCursor(Qt.OpenHandCursor)

    def zoom_by(self, factor: float) -> None:
        self.zoom = float(np.clip(self.zoom * factor, 0.5, 8.0)); self.update()

    def rotate_by(self, degrees: float) -> None:
        self.rotation_degrees = (self.rotation_degrees + float(degrees)) % 360.0; self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0; self.rotation_degrees = 0.0; self.pan = QPointF(); self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15); event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = event.position(); self.setCursor(Qt.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_position is not None:
            current = event.position(); self.pan += current - self._drag_position; self._drag_position = current; self.update(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = None; self.setCursor(Qt.OpenHandCursor); event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        """Map a display click back to one validated dose-grid voxel."""
        if self.data is None or min(self._image_size) <= 0 or min(self._display_size) <= 0:
            return
        transform = QTransform()
        transform.translate(self._display_center.x(), self._display_center.y())
        transform.rotate(self.rotation_degrees); transform.scale(self.zoom, self.zoom)
        inverse, valid = transform.inverted()
        if not valid:
            return
        local = inverse.map(event.position())
        display_width, display_height = self._display_size
        column = int(np.floor((local.x() + display_width / 2.0) * self._image_size[0] / display_width))
        row = int(np.floor((local.y() + display_height / 2.0) * self._image_size[1] / display_height))
        column = int(np.clip(column, 0, self._image_size[0] - 1)); row = int(np.clip(row, 0, self._image_size[1] - 1))
        if self.orientation == "axial":
            voxel = (self.index, row, column)
        elif self.orientation == "sagittal":
            voxel = (row, column, self.index)
        else:
            voxel = (row, self.index, column)
        self.voxelSelected.emit(*voxel); event.accept()

    def set_view(self, data: Layer31ViewerData, field: str, index: int, roi: str, show_structures: bool, show_warning: bool,
                 scalar_range: tuple[float, float] | None = None, crosshair: tuple[int, int, int] | None = None,
                 visible_rois: list[str] | None = None) -> None:
        self.data, self.field, self.index, self.roi = data, field, index, roi
        self.show_structures, self.show_warning, self.scalar_range, self.crosshair = show_structures, show_warning, scalar_range, crosshair
        self.visible_rois = list(visible_rois or []); self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#071a38"))
        if self.data is None or not self.field:
            painter.setPen(QColor("#d6e2ef")); painter.drawText(self.rect(), Qt.AlignCenter, "No Layer 3.1 field")
            return
        field = self.data.fields[self.field]; plane = np.asarray(_slice(field, self.orientation, self.index))
        low, high = self.scalar_range or tuple(self.data.field_metadata[self.field]["display_range"])
        meta = self.data.field_metadata[self.field]
        rgb = _colour_map(plane, low, high, str(meta.get("palette") or "biological_lq"))
        if self.show_structures:
            overlay_colours = {
                "Region: Whole GTV": [255, 220, 70], "Region: Vertices": [232, 93, 117],
                "Region: Valleys": [51, 181, 165], "Region: Other GTV": [118, 137, 222],
            }
            for name in self.visible_rois:
                if name not in self.data.masks: continue
                mask = np.asarray(_slice(self.data.masks[name], self.orientation, self.index), dtype=bool)
                rgb[mask & ~ndimage.binary_erosion(mask)] = overlay_colours.get(name, [220, 230, 240])
            if self.roi in self.data.masks:
                mask = np.asarray(_slice(self.data.masks[self.roi], self.orientation, self.index), dtype=bool)
                rgb[mask & ~ndimage.binary_erosion(mask)] = [255, 255, 255]
        if self.show_warning and "LQ_high_dose_warning_mask" in self.data.fields:
            warning = np.asarray(_slice(self.data.fields["LQ_high_dose_warning_mask"], self.orientation, self.index), dtype=bool)
            rgb[warning & ~ndimage.binary_erosion(warning)] = [255, 70, 60]
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
        target = self.rect().adjusted(10, 35, -10, -50)
        scaled = image.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_size = (image.width(), image.height()); self._display_size = (scaled.width(), scaled.height())
        self._display_center = QPointF(target.center()) + self.pan
        painter.save(); painter.setClipRect(target)
        center = self._display_center
        painter.translate(center); painter.rotate(self.rotation_degrees); painter.scale(self.zoom, self.zoom)
        origin = QPointF(-scaled.width() / 2.0, -scaled.height() / 2.0)
        painter.drawImage(origin, scaled)
        if self.crosshair is not None:
            z_index, y_index, x_index = self.crosshair
            if self.orientation == "axial": row, column = y_index, x_index
            elif self.orientation == "sagittal": row, column = z_index, y_index
            else: row, column = z_index, x_index
            x_pos = origin.x() + (column + 0.5) * scaled.width() / max(image.width(), 1)
            y_pos = origin.y() + (row + 0.5) * scaled.height() / max(image.height(), 1)
            painter.setPen(QPen(QColor("#ffffff"), max(1.0 / self.zoom, 0.35), Qt.DashLine))
            painter.drawLine(QPointF(origin.x(), y_pos), QPointF(origin.x() + scaled.width(), y_pos))
            painter.drawLine(QPointF(x_pos, origin.y()), QPointF(x_pos, origin.y() + scaled.height()))
        painter.restore()
        painter.setPen(QColor("#eef5fb")); painter.drawText(10, 22, f"{self.orientation.upper()} · slice {self.index}")
        bar_left, bar_right, bar_y = 66, max(self.width() - 66, 67), self.height() - 34
        width = max(bar_right - bar_left, 1)
        colours = _colour_map(np.linspace(low, high, width)[None, :], low, high, str(meta.get("palette") or "biological_lq"))[0]
        for offset, colour in enumerate(colours):
            painter.setPen(QColor(*map(int, colour))); painter.drawLine(bar_left + offset, bar_y, bar_left + offset, bar_y + 8)
        painter.setPen(QColor("#eef5fb")); painter.drawText(8, bar_y + 8, f"{low:.3g}"); painter.drawText(bar_right + 4, bar_y + 8, f"{high:.3g}")
        painter.drawText(10, self.height() - 48, f"Complete 3D range · {meta['units']} · zoom {self.zoom:.2g}× · rotation {self.rotation_degrees:.0f}° · double-click selects voxel")


class BiologyColorBar(QWidget):
    """Shared quantitative legend for the 2D and 3D biological displays."""

    def __init__(self) -> None:
        super().__init__(); self.meta: dict[str, Any] = {}; self.display_range = (0.0, 1.0); self.actual_range = (0.0, 1.0)
        self.setMinimumHeight(62); self.setMaximumHeight(78)

    def set_scale(self, meta: dict[str, Any], display_range: tuple[float, float], actual_range: tuple[float, float]) -> None:
        self.meta = dict(meta); self.display_range = display_range; self.actual_range = actual_range; self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#f8fbfe"))
        if not self.meta:
            painter.setPen(QColor("#62758a")); painter.drawText(self.rect(), Qt.AlignCenter, "No quantitative field selected"); return
        low, high = self.display_range; actual_low, actual_high = self.actual_range
        left, right, top = 18, max(self.width() - 18, 19), 25; width = max(right - left, 1)
        colours = _colour_map(np.linspace(low, high, width)[None, :], low, high, str(self.meta.get("palette") or "biological_lq"))[0]
        for offset, colour in enumerate(colours):
            painter.setPen(QColor(*map(int, colour))); painter.drawLine(left + offset, top, left + offset, top + 13)
        painter.setPen(QColor("#13263a")); painter.drawText(left, 16, f"{self.meta.get('label', 'Field')} [{self.meta.get('units', '')}]")
        painter.drawText(left, 54, f"display {low:.4g}–{high:.4g}   ·   actual {actual_low:.4g}–{actual_high:.4g}")
        painter.drawText(max(right - 150, left), 16, "LOW EFFECT   →   HIGH EFFECT")
        painter.setPen(QColor("#7c8794")); painter.drawText(left, top + 13, "◁ below"); painter.drawText(right - 122, top + 13, "above ▷   invalid ▨")


def _vertex_colour_renderer(mesh: Any, normals: np.ndarray, parent: Any) -> Any:
    vertices = np.asarray(mesh.vertices_lps_mm, dtype=np.float32)
    colours = np.column_stack((np.asarray(mesh.rgb, dtype=np.float32) / 255.0, np.ones(len(vertices), dtype=np.float32)))
    packed = np.ascontiguousarray(np.column_stack((vertices, normals, colours)), dtype=np.float32)
    indices = np.ascontiguousarray(mesh.faces.reshape(-1), dtype=np.uint32)
    geometry = Qt3DCore.QGeometry(parent)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    for name, offset, size in (
        (Qt3DCore.QAttribute.defaultPositionAttributeName(), 0, 3),
        (Qt3DCore.QAttribute.defaultNormalAttributeName(), 12, 3),
        (Qt3DCore.QAttribute.defaultColorAttributeName(), 24, 4),
    ):
        attribute = Qt3DCore.QAttribute(geometry); attribute.setName(name)
        attribute.setVertexBaseType(Qt3DCore.QAttribute.Float); attribute.setVertexSize(size)
        attribute.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); attribute.setBuffer(vertex_buffer)
        attribute.setByteOffset(offset); attribute.setByteStride(40); attribute.setCount(len(vertices)); geometry.addAttribute(attribute)
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry); index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt)
    index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute); index.setBuffer(index_buffer); index.setCount(len(indices)); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent); renderer.setGeometry(geometry)
    renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Triangles); renderer.setVertexCount(len(indices)); return renderer


def _solid_surface_renderer(mesh: Any, normals: np.ndarray, parent: Any, faces: np.ndarray | None = None) -> Any:
    """Create a reliable position/normal renderer for Phong CAD materials."""
    vertices = np.asarray(mesh.vertices_lps_mm, dtype=np.float32)
    normal_values = np.asarray(normals, dtype=np.float32)
    selected_faces = np.asarray(mesh.faces if faces is None else faces, dtype=np.uint32)
    packed = np.ascontiguousarray(np.column_stack((vertices, normal_values)), dtype=np.float32)
    indices = np.ascontiguousarray(selected_faces.reshape(-1), dtype=np.uint32)
    geometry = Qt3DCore.QGeometry(parent)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    for name, offset in (
        (Qt3DCore.QAttribute.defaultPositionAttributeName(), 0),
        (Qt3DCore.QAttribute.defaultNormalAttributeName(), 12),
    ):
        attribute = Qt3DCore.QAttribute(geometry); attribute.setName(name)
        attribute.setVertexBaseType(Qt3DCore.QAttribute.Float); attribute.setVertexSize(3)
        attribute.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); attribute.setBuffer(vertex_buffer)
        attribute.setByteOffset(offset); attribute.setByteStride(24); attribute.setCount(len(vertices)); geometry.addAttribute(attribute)
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry); index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt)
    index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute); index.setBuffer(index_buffer)
    index.setCount(len(indices)); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent); renderer.setGeometry(geometry)
    renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Triangles); renderer.setVertexCount(len(indices))
    return renderer


def _line_renderer(vertices: np.ndarray, edges: np.ndarray, parent: Any) -> Any:
    geometry = Qt3DCore.QGeometry(parent)
    positions = np.ascontiguousarray(vertices, dtype=np.float32)
    normals = np.tile(np.asarray([[0.0, 0.0, 1.0]], dtype=np.float32), (len(positions), 1))
    packed = np.ascontiguousarray(np.column_stack((positions, normals)), dtype=np.float32)
    vertex_buffer = Qt3DCore.QBuffer(geometry); vertex_buffer.setData(QByteArray(packed.tobytes()))
    for name, offset in ((Qt3DCore.QAttribute.defaultPositionAttributeName(), 0), (Qt3DCore.QAttribute.defaultNormalAttributeName(), 12)):
        attribute = Qt3DCore.QAttribute(geometry); attribute.setName(name)
        attribute.setVertexBaseType(Qt3DCore.QAttribute.Float); attribute.setVertexSize(3)
        attribute.setAttributeType(Qt3DCore.QAttribute.VertexAttribute); attribute.setBuffer(vertex_buffer)
        attribute.setByteOffset(offset); attribute.setByteStride(24); attribute.setCount(len(positions)); geometry.addAttribute(attribute)
    indices = np.ascontiguousarray(edges.reshape(-1), dtype=np.uint32)
    index_buffer = Qt3DCore.QBuffer(geometry); index_buffer.setData(QByteArray(indices.tobytes()))
    index = Qt3DCore.QAttribute(geometry); index.setVertexBaseType(Qt3DCore.QAttribute.UnsignedInt)
    index.setAttributeType(Qt3DCore.QAttribute.IndexAttribute); index.setBuffer(index_buffer); index.setCount(len(indices)); geometry.addAttribute(index)
    renderer = Qt3DRender.QGeometryRenderer(parent); renderer.setGeometry(geometry)
    renderer.setPrimitiveType(Qt3DRender.QGeometryRenderer.Lines); renderer.setVertexCount(len(indices)); return renderer


class SoftwareBiologicalScene3D(QWidget):
    """Crash-resistant macOS CAD projection using Qt's raster paint engine.

    Mesh generation, DICOM-LPS coordinates, scalar sampling, and exports remain
    authoritative and unchanged.  Only final interactive presentation is
    projected in software, avoiding the Qt3D/Metal pipeline that can terminate
    the Python process inside Apple's render driver.
    """

    pointPicked = Signal(float, float, float)

    def __init__(self) -> None:
        super().__init__()
        self._bundle: CADSceneBundle | None = None
        self._focused_name: str | None = None
        self.center = np.zeros(3, dtype=float)
        self.distance = 100.0
        self.yaw = -35.0; self.pitch = 24.0; self.zoom = 1.0
        self.pan = QPointF(); self._drag: QPointF | None = None; self._pan_drag = False
        self.selected_world_position: np.ndarray | None = None
        self._projected_pick_points = np.empty((0, 2), dtype=float)
        self._projected_world_points = np.empty((0, 3), dtype=float)
        self.setMinimumSize(620, 500); self.setMouseTracking(True); self.setCursor(Qt.OpenHandCursor)

    def clear(self) -> None:
        self._bundle = None
        self._projected_pick_points = np.empty((0, 2), dtype=float)
        self._projected_world_points = np.empty((0, 3), dtype=float)
        self.update()

    @staticmethod
    def _anatomical_style(name: str, focused: bool) -> tuple[QColor, float]:
        return BiologicalScene3D._anatomical_style(name, focused)

    def set_bundle(self, bundle: CADSceneBundle, focused_name: str | None = None) -> None:
        self._bundle = bundle; self._focused_name = focused_name
        vertices = []
        for result in [*bundle.anatomy_meshes.values(), *bundle.special_meshes.values(), *([bundle.overlay_mesh] if bundle.overlay_mesh else [])]:
            if result and result.display_surface is not None:
                values = np.asarray(result.display_surface.vertices_lps_mm, dtype=float)
                if values.size: vertices.append(values)
        if vertices:
            combined = np.vstack(vertices); low, high = np.nanmin(combined, axis=0), np.nanmax(combined, axis=0)
            self.center = (low + high) / 2.0; self.distance = max(float(np.linalg.norm(high - low)), 1.0)
        self.update()

    def set_selected_world_position(self, point_lps_mm: tuple[float, float, float] | None) -> None:
        self.selected_world_position = None if point_lps_mm is None else np.asarray(point_lps_mm, dtype=float)
        self.update()

    def set_view(self, orientation: str) -> None:
        values = {"axial": (0.0, 0.0), "sagittal": (90.0, 0.0), "coronal": (0.0, 90.0), "perspective": (-35.0, 24.0)}
        self.yaw, self.pitch = values.get(orientation, values["perspective"]); self.pan = QPointF(); self.update()

    def zoom_by(self, factor: float) -> None:
        # Native scene used camera-distance factors: values below one zoom in.
        self.zoom = float(np.clip(self.zoom / max(float(factor), 1.0e-6), 0.2, 12.0)); self.update()

    def rotate_by(self, degrees: float) -> None:
        self.yaw = (self.yaw + float(degrees)) % 360.0; self.update()

    def wheelEvent(self, event: Any) -> None:
        self.zoom_by(0.86 if event.angleDelta().y() > 0 else 1.16); event.accept()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self._drag = event.position(); self._pan_drag = event.button() != Qt.LeftButton
            self.setCursor(Qt.ClosedHandCursor); event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag is None: return
        delta = event.position() - self._drag; self._drag = event.position()
        if self._pan_drag: self.pan += delta
        else:
            self.yaw += delta.x() * 0.45; self.pitch = float(np.clip(self.pitch + delta.y() * 0.45, -89.0, 89.0))
        self.update(); event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._drag is not None:
            moved = event.position() - self._drag
            if event.button() == Qt.LeftButton and abs(moved.x()) + abs(moved.y()) < 3:
                self._pick(event.position())
        self._drag = None; self.setCursor(Qt.OpenHandCursor); event.accept()

    def _rotation(self) -> np.ndarray:
        yaw, pitch = np.deg2rad(self.yaw), np.deg2rad(self.pitch)
        rz = np.asarray([[np.cos(yaw), -np.sin(yaw), 0.0], [np.sin(yaw), np.cos(yaw), 0.0], [0.0, 0.0, 1.0]])
        rx = np.asarray([[1.0, 0.0, 0.0], [0.0, np.cos(pitch), -np.sin(pitch)], [0.0, np.sin(pitch), np.cos(pitch)]])
        return rx @ rz

    def _project(self, world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rotated = (np.asarray(world, dtype=float) - self.center) @ self._rotation().T
        scale = 0.82 * min(max(self.width(), 1), max(self.height(), 1)) / max(self.distance, 1.0) * self.zoom
        screen = np.column_stack((self.width() / 2.0 + self.pan.x() + rotated[:, 0] * scale,
                                  self.height() / 2.0 + self.pan.y() - rotated[:, 1] * scale))
        return screen, rotated[:, 2]

    def _mesh_triangles(self, result: BiologicalMeshResult, colour: QColor | None, alpha: float, overlay: bool) -> list[tuple[float, QPolygonF, QColor]]:
        surface = result.display_surface
        if surface is None: return []
        vertices = np.asarray(surface.vertices_lps_mm, dtype=float); faces = np.asarray(surface.faces, dtype=np.int64)
        if not len(vertices) or not len(faces): return []
        # Bound raster workload deterministically. Geometry and exported meshes
        # stay complete; only display triangles are decimated.
        stride = max(int(np.ceil(len(faces) / 12000.0)), 1); faces = faces[::stride]
        screen, depth = self._project(vertices); rgb = np.asarray(surface.rgb, dtype=np.uint8)
        records = []
        for face in faces:
            points = QPolygonF([QPointF(float(screen[index, 0]), float(screen[index, 1])) for index in face])
            if overlay and len(rgb) == len(vertices):
                mean = np.mean(rgb[face], axis=0).astype(int); face_colour = QColor(*map(int, mean))
            else: face_colour = QColor(colour or QColor("#8fb5ce"))
            face_colour.setAlphaF(float(np.clip(alpha, 0.0, 1.0)))
            records.append((float(np.mean(depth[face])), points, face_colour))
        self._projected_pick_points = screen[::max(len(screen) // 5000, 1)]
        self._projected_world_points = vertices[::max(len(vertices) // 5000, 1)]
        return records

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#071a38")); painter.setRenderHint(QPainter.Antialiasing, True)
        bundle = self._bundle
        if bundle is None:
            painter.setPen(QColor("#d6e2ef")); painter.drawText(self.rect(), Qt.AlignCenter, "No biological CAD scene")
            return
        triangles: list[tuple[float, QPolygonF, QColor]] = []
        for name, result in bundle.anatomy_meshes.items():
            if bundle.overlay_mesh is not None and name == bundle.overlay_target: continue
            colour, alpha = self._anatomical_style(name, name == self._focused_name)
            if name == "Region: Whole GTV": alpha = bundle.gtv_opacity * (1.0 if name == self._focused_name else 0.32)
            elif name.startswith("OAR:"): alpha = bundle.oar_opacity * (1.5 if name == self._focused_name else 1.0)
            triangles.extend(self._mesh_triangles(result, colour, alpha, False))
        if bundle.overlay_mesh is not None:
            triangles.extend(self._mesh_triangles(bundle.overlay_mesh, None, bundle.gtv_opacity, True))
        for result in bundle.special_meshes.values():
            triangles.extend(self._mesh_triangles(result, None, bundle.isosurface_opacity, True))
        painter.setPen(Qt.NoPen)
        for _depth, polygon, colour in sorted(triangles, key=lambda item: item[0]):
            painter.setBrush(colour); painter.drawPolygon(polygon)
        painter.setPen(QPen(QColor("#24d6a5"), 2.0))
        for start, end in bundle.graph_edges_lps_mm:
            projected, _ = self._project(np.asarray([start, end], dtype=float)); painter.drawLine(QPointF(*projected[0]), QPointF(*projected[1]))
        painter.setBrush(QColor("#ffe13a")); painter.setPen(Qt.NoPen)
        for point in bundle.vertex_centres_lps_mm:
            projected, _ = self._project(np.asarray([point], dtype=float)); painter.drawEllipse(QPointF(*projected[0]), 4.5, 4.5)
        if self.selected_world_position is not None and np.isfinite(self.selected_world_position).all():
            projected, _ = self._project(self.selected_world_position.reshape(1, 3)); painter.setBrush(QColor("#ffffff")); painter.drawEllipse(QPointF(*projected[0]), 6.0, 6.0)
        painter.setPen(QColor("#d6e2ef")); painter.drawText(12, 22, f"Software CAD · DICOM LPS · zoom {self.zoom:.2g}× · yaw {self.yaw:.0f}° · pitch {self.pitch:.0f}°")

    def _pick(self, point: QPointF) -> None:
        if not len(self._projected_pick_points): return
        delta = self._projected_pick_points - np.asarray([point.x(), point.y()])
        index = int(np.argmin(np.sum(delta * delta, axis=1)))
        if float(np.linalg.norm(delta[index])) <= 18.0:
            world = self._projected_world_points[index]; self.pointPicked.emit(*map(float, world))


class BiologicalScene3D(QWidget):
    pointPicked = Signal(float, float, float)

    def __init__(self) -> None:
        super().__init__(); self.window = Qt3DExtras.Qt3DWindow(); self.window.defaultFrameGraph().setClearColor(QColor("#071a38"))
        self.container = QWidget.createWindowContainer(self.window, self); layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.container)
        self.root = Qt3DCore.QEntity(); self.window.setRootEntity(self.root); self.camera = self.window.camera()
        self.camera.lens().setPerspectiveProjection(35.0, 16/9, 0.1, 10000.0)
        self.controls = Qt3DExtras.QOrbitCameraController(self.root); self.controls.setCamera(self.camera)
        self.controls.setLinearSpeed(80.0); self.controls.setLookSpeed(140.0)
        self.entities: list[Any] = []; self.center = np.zeros(3); self.distance = 100.0; self.setMinimumSize(620, 500)
        self._bundle: CADSceneBundle | None = None
        self.selected_world_position: np.ndarray | None = None

    def clear(self) -> None:
        # Qt3D finishes QNode construction through posted events. Detaching a
        # newly-created entity before those events run can leave Qt's private
        # post-constructor event holding a dead parent and cause a native
        # QNodePrivate::_q_postConstructorInit segmentation fault. Disable the
        # old actors immediately but retain their scene parent until Qt handles
        # deleteLater in event order.
        for entity in self.entities:
            entity.setEnabled(False)
            entity.deleteLater()
        self.entities = []

    @staticmethod
    def _anatomical_style(name: str, focused: bool) -> tuple[QColor, float]:
        if name == "Region: Whole GTV":
            return QColor("#f4d77a"), 0.20 if not focused else 0.34
        if name == "Region: Vertices":
            return QColor("#20a6c9"), 0.90 if not focused else 1.0
        if name == "Region: Valleys":
            return QColor("#6e5bd8"), 0.74 if not focused else 0.95
        if name.startswith("OAR:"):
            if name.split(":", 1)[-1].strip().upper() in {"BODY", "EXTERNAL", "BODY-PTV"}:
                return QColor("#a8b3bf"), 0.08 if not focused else 0.18
            return QColor("#ed78b5"), 0.34 if not focused else 0.62
        return QColor("#8fb5ce"), 0.42 if not focused else 0.72

    def _add_surface(self, result: BiologicalMeshResult, colour: QColor, alpha: float) -> None:
        if result.display_surface is None or result.vertex_normals is None:
            return
        entity = Qt3DCore.QEntity(self.root)
        entity.addComponent(_solid_surface_renderer(result.display_surface, result.vertex_normals, entity))
        entity.addComponent(_material(entity, colour, alpha))
        self._attach_picker(entity)
        self.entities.append(entity)

    def _attach_picker(self, entity: Any) -> None:
        picker = Qt3DRender.QObjectPicker(entity); picker.setHoverEnabled(False)
        def emit_point(event: Any) -> None:
            point = event.worldIntersection()
            self.pointPicked.emit(float(point.x()), float(point.y()), float(point.z()))
        picker.clicked.connect(emit_point); entity.addComponent(picker)

    def _add_scalar_overlay(self, result: BiologicalMeshResult, alpha: float = 0.72) -> None:
        """Render scalar bands with Phong materials instead of fragile per-vertex material."""
        surface = result.display_surface
        if surface is None or result.vertex_normals is None:
            return
        faces = np.asarray(surface.faces, dtype=np.uint32)
        values = np.asarray(surface.scalar_values, dtype=float)
        face_values = np.nanmean(values[faces], axis=1)
        finite = np.isfinite(face_values)
        if not finite.any():
            return
        configured_range = result.provenance.get("display_scalar_range")
        if isinstance(configured_range, list) and len(configured_range) == 2:
            low, high = map(float, configured_range)
        else:
            low, high = float(np.nanmin(values)), float(np.nanmax(values))
        if high <= low:
            self._add_surface(result, QColor("#33a884"), alpha)
            return
        band_count = 10
        band = np.zeros(len(face_values), dtype=int)
        band[finite] = np.clip(
            ((face_values[finite] - low) / (high - low) * band_count).astype(int),
            0, band_count - 1,
        )
        for index in range(band_count):
            selection = finite & (band == index)
            if not selection.any():
                continue
            selected_faces = faces[selection]
            colour_values = np.asarray(surface.rgb, dtype=np.uint8)[selected_faces].reshape(-1, 3)
            mean_colour = np.round(np.mean(colour_values, axis=0)).astype(int)
            entity = Qt3DCore.QEntity(self.root)
            entity.addComponent(_solid_surface_renderer(surface, result.vertex_normals, entity, selected_faces))
            entity.addComponent(_material(entity, QColor(*map(int, mean_colour)), alpha))
            self._attach_picker(entity)
            self.entities.append(entity)

    def _add_scalar_contours(self, result: BiologicalMeshResult) -> None:
        surface = result.display_surface
        if surface is None: return
        values = np.asarray(surface.scalar_values, dtype=float); finite = np.isfinite(values)
        if not finite.any(): return
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
        if high <= low: return
        bins = np.full(len(values), -1, dtype=int)
        bins[finite] = np.clip(((values[finite] - low) / (high - low) * 10).astype(int), 0, 9)
        faces = np.asarray(surface.faces, dtype=np.uint32)
        edges = np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
        crossing = finite[edges[:, 0]] & finite[edges[:, 1]] & (bins[edges[:, 0]] != bins[edges[:, 1]])
        edges = np.unique(np.sort(edges[crossing], axis=1), axis=0)
        if not len(edges): return
        entity = Qt3DCore.QEntity(self.root); entity.addComponent(_line_renderer(surface.vertices_lps_mm, edges, entity))
        entity.addComponent(_material(entity, QColor("#f4f8fc"), 0.82)); self.entities.append(entity)

    def _add_vertex_centres(self, centres: tuple[tuple[float, float, float], ...]) -> None:
        for point in centres:
            entity = Qt3DCore.QEntity(self.root); sphere = Qt3DExtras.QSphereMesh(entity)
            sphere.setRadius(max(self.distance * 0.009, 0.65)); sphere.setRings(12); sphere.setSlices(18)
            transform = Qt3DCore.QTransform(entity); transform.setTranslation(QVector3D(*map(float, point)))
            entity.addComponent(sphere); entity.addComponent(transform)
            entity.addComponent(_material(entity, QColor("#ffe13a"), 1.0)); self.entities.append(entity)

    def _add_graph(self, segments: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...]) -> None:
        if not segments: return
        vertices = np.asarray([point for segment in segments for point in segment], dtype=np.float32)
        edges = np.arange(len(vertices), dtype=np.uint32).reshape(-1, 2)
        entity = Qt3DCore.QEntity(self.root); entity.addComponent(_line_renderer(vertices, edges, entity))
        entity.addComponent(_material(entity, QColor("#24d6a5"), 0.96)); self.entities.append(entity)

    def set_bundle(self, bundle: CADSceneBundle, focused_name: str | None = None) -> None:
        self.clear()
        self._bundle = bundle
        all_vertices: list[np.ndarray] = []
        for name, result in bundle.anatomy_meshes.items():
            if result.display_surface is None:
                continue
            # The biological overlay replaces the solid GTV skin while all
            # other anatomical structures remain visible in the same LPS scene.
            if bundle.overlay_mesh is not None and name == bundle.overlay_target:
                continue
            colour, alpha = self._anatomical_style(name, name == focused_name)
            if name == "Region: Whole GTV":
                alpha = 0.08 if bundle.mode == "ISOSURFACE" else bundle.gtv_opacity * (1.0 if name == focused_name else 0.32)
            elif name.startswith("OAR:"): alpha = bundle.oar_opacity * (1.5 if name == focused_name else 1.0)
            self._add_surface(result, colour, alpha)
            all_vertices.append(np.asarray(result.display_surface.vertices_lps_mm))
        if bundle.overlay_mesh is not None and bundle.overlay_mesh.display_surface is not None:
            self._add_scalar_overlay(bundle.overlay_mesh, alpha=bundle.gtv_opacity)
            if bundle.show_contours: self._add_scalar_contours(bundle.overlay_mesh)
            all_vertices.append(np.asarray(bundle.overlay_mesh.display_surface.vertices_lps_mm))
        for _label, result in bundle.special_meshes.items():
            if result.display_surface is None:
                continue
            self._add_scalar_overlay(result, alpha=bundle.isosurface_opacity)
            if bundle.show_contours: self._add_scalar_contours(result)
            all_vertices.append(np.asarray(result.display_surface.vertices_lps_mm))
        if not all_vertices:
            return
        vertices = np.vstack(all_vertices); low, high = np.min(vertices, axis=0), np.max(vertices, axis=0)
        self.center = (low + high) / 2; self.distance = max(float(np.linalg.norm(high - low)) * 1.5, 20.0)
        self.camera.lens().setPerspectiveProjection(35.0, 16/9, max(self.distance / 1000.0, 0.01), self.distance * 30.0)
        self.set_view("perspective")
        for direction, intensity in ((np.ones(3), 1.05), (np.asarray([-1., 1., -0.5]), 0.65)):
            light_entity = Qt3DCore.QEntity(self.root); light = Qt3DRender.QPointLight(light_entity); light.setIntensity(intensity)
            transform = Qt3DCore.QTransform(light_entity)
            transform.setTranslation(QVector3D(*map(float, self.center + direction * self.distance)))
            light_entity.addComponent(light); light_entity.addComponent(transform); self.entities.append(light_entity)
        self._add_graph(bundle.graph_edges_lps_mm)
        self._add_vertex_centres(bundle.vertex_centres_lps_mm)
        self._add_selection_marker()

    def set_selected_world_position(self, point_lps_mm: tuple[float, float, float] | None) -> None:
        self.selected_world_position = None if point_lps_mm is None else np.asarray(point_lps_mm, dtype=float)
        if self._bundle is not None:
            self.set_bundle(self._bundle)

    def _add_selection_marker(self) -> None:
        if self.selected_world_position is None or not np.isfinite(self.selected_world_position).all():
            return
        entity = Qt3DCore.QEntity(self.root); sphere = Qt3DExtras.QSphereMesh(entity)
        sphere.setRadius(max(self.distance * 0.012, 0.8)); sphere.setRings(14); sphere.setSlices(20)
        transform = Qt3DCore.QTransform(entity); transform.setTranslation(QVector3D(*map(float, self.selected_world_position)))
        entity.addComponent(sphere); entity.addComponent(transform); entity.addComponent(_material(entity, QColor("#ffffff"), 1.0))
        self.entities.append(entity)

    def set_view(self, orientation: str) -> None:
        directions = {"axial": np.array([0., 0., 1.]), "sagittal": np.array([1., 0., 0.]), "coronal": np.array([0., 1., 0.])}
        direction = directions.get(orientation, np.array([1., -1., 1.]) / np.sqrt(3))
        self.camera.setViewCenter(QVector3D(*map(float, self.center))); self.camera.setPosition(QVector3D(*map(float, self.center + direction * self.distance)))
        self.camera.setUpVector(QVector3D(0, 0, 1) if orientation != "axial" else QVector3D(0, -1, 0))

    def zoom_by(self, factor: float) -> None:
        position = np.asarray([self.camera.position().x(), self.camera.position().y(), self.camera.position().z()], dtype=float)
        vector = position - self.center
        length = float(np.linalg.norm(vector))
        if length <= 0: return
        new_length = float(np.clip(length * factor, max(self.distance * 0.08, 1.0), self.distance * 12.0))
        self.camera.setPosition(QVector3D(*map(float, self.center + vector / length * new_length)))

    def rotate_by(self, degrees: float) -> None:
        position = np.asarray([self.camera.position().x(), self.camera.position().y(), self.camera.position().z()], dtype=float)
        vector = position - self.center; radians = np.deg2rad(float(degrees))
        rotated = np.asarray([np.cos(radians) * vector[0] - np.sin(radians) * vector[1],
                              np.sin(radians) * vector[0] + np.cos(radians) * vector[1], vector[2]])
        self.camera.setPosition(QVector3D(*map(float, self.center + rotated)))
        self.camera.setViewCenter(QVector3D(*map(float, self.center)))


class _MeshSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class _MeshWorker(QRunnable):
    def __init__(self, generation: int, operation: Any) -> None:
        super().__init__(); self.generation = generation; self.operation = operation; self.signals = _MeshSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(self.generation, self.operation())
        except Exception as exc:
            self.signals.failed.emit(self.generation, str(exc))


class RegionalResultCard(QFrame):
    """Compact, non-authoritative presentation of one stored regional record."""
    selected = Signal(str)

    def __init__(self, region_id: str, title: str) -> None:
        super().__init__(); self.region_id = region_id; self.setObjectName("metricCard")
        self.setCursor(Qt.PointingHandCursor); self.setMinimumWidth(165)
        layout = QVBoxLayout(self); self.title = QLabel(title); self.title.setObjectName("metricTitle")
        self.volume = QLabel("Volume  —"); self.survival = QLabel("Mean SF  —"); self.contribution = QLabel("Contribution  —")
        layout.addWidget(self.title); layout.addWidget(self.volume); layout.addWidget(self.survival); layout.addWidget(self.contribution)

    def set_record(self, record: dict[str, Any] | None) -> None:
        record = record or {}
        fraction = record.get("tumour_volume_fraction"); survival = record.get("mean_surviving_fraction")
        contribution = record.get("survivor_contribution_fraction")
        self.volume.setText(f"Volume  {100.0 * float(fraction):.2f}%" if fraction is not None else "Volume  —")
        self.survival.setText(f"Mean SF  {float(survival):.4g}" if survival is not None else "Mean SF  —")
        self.contribution.setText(f"Contribution  {100.0 * float(contribution):.2f}%" if contribution is not None else "Contribution  —")

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.region_id); event.accept(); return
        super().mousePressEvent(event)


class SurvivalContributionBar(QWidget):
    """Clickable 100% bar over the stored regional survivor contributions."""
    selected = Signal(str)
    COLOURS = {"H": QColor("#e85d75"), "V": QColor("#33b5a5"), "O": QColor("#7689de")}
    LABELS = {"H": "Vertex", "V": "Valley", "O": "Other GTV"}

    def __init__(self) -> None:
        super().__init__(); self.records: list[dict[str, Any]] = []; self.setMinimumHeight(92); self.setCursor(Qt.PointingHandCursor)

    def set_records(self, records: list[dict[str, Any]]) -> None:
        self.records = list(records); self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#13263a")); painter.drawText(0, 16, "Residual tumour-survival contribution")
        bar = QRectF(0, 27, max(self.width(), 1), 24); offset = 0.0
        total = sum(max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) for item in self.records)
        denominator = total if total > 0 else 1.0
        for item in self.records:
            region = str(item.get("region_id")); value = max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) / denominator
            width = bar.width() * value; painter.fillRect(QRectF(offset, bar.y(), width, bar.height()), self.COLOURS.get(region, QColor("#718096"))); offset += width
        painter.setPen(QPen(QColor("#9fb0c1"), 1)); painter.drawRect(bar)
        x = 0
        for item in self.records:
            region = str(item.get("region_id")); value = 100.0 * float(item.get("survivor_contribution_fraction") or 0.0)
            painter.setPen(self.COLOURS.get(region, QColor("#718096"))); painter.drawText(x, 76, f"■ {self.LABELS.get(region, region)} {value:.2f}%")
            x += max(self.width() // 3, 150)

    def mousePressEvent(self, event: Any) -> None:
        if not self.records or event.button() != Qt.LeftButton: return
        total = sum(max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) for item in self.records)
        if total <= 0: return
        position = float(event.position().x()) / max(float(self.width()), 1.0); cumulative = 0.0
        for item in self.records:
            cumulative += max(float(item.get("survivor_contribution_fraction") or 0.0), 0.0) / total
            if position <= cumulative:
                self.selected.emit(str(item.get("region_id"))); break


class SurvivalDistributionCanvas(QWidget):
    """Display-only regional histogram of −log10(SF) from stored fields."""
    COLOURS = SurvivalContributionBar.COLOURS

    def __init__(self) -> None:
        super().__init__(); self.series: dict[str, np.ndarray] = {}; self.setMinimumHeight(150)

    def set_data(self, data: Layer31ViewerData | None) -> None:
        self.series = {}
        if data is not None and "negative_log10_survival_MLQ" in data.fields:
            values = data.fields["negative_log10_survival_MLQ"]
            for region, name in (("H", "Region: Vertices"), ("V", "Region: Valleys"), ("O", "Region: Other GTV")):
                mask = data.masks.get(name)
                if mask is not None and np.any(mask): self.series[region] = np.asarray(values[mask], dtype=float)
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#f8fbfe")); painter.setPen(QColor("#13263a"))
        painter.drawText(10, 18, "Regional MLQ survival distribution · −log₁₀(SF)")
        plot = self.rect().adjusted(42, 28, -12, -28)
        if not self.series:
            painter.setPen(QColor("#62758a")); painter.drawText(plot, Qt.AlignCenter, "No stored MLQ survival field"); return
        finite = np.concatenate([item[np.isfinite(item)] for item in self.series.values()]); maximum = max(float(np.max(finite)), 1.0)
        bins = np.linspace(0.0, maximum, 33); band = plot.height() / max(len(self.series), 1)
        for row, (region, values) in enumerate(self.series.items()):
            counts, _ = np.histogram(values[np.isfinite(values)], bins=bins); peak = max(int(np.max(counts)), 1); y0 = plot.top() + row * band
            for index, count in enumerate(counts):
                width = plot.width() / len(counts); height = (band - 8) * float(count) / peak
                painter.fillRect(QRectF(plot.left() + index * width, y0 + band - height - 4, max(width - 1, 1), height), self.COLOURS[region])
            painter.setPen(self.COLOURS[region]); painter.drawText(4, int(y0 + band / 2), region)
        painter.setPen(QColor("#62758a")); painter.drawText(plot.left(), self.height() - 6, "0"); painter.drawText(plot.right() - 45, self.height() - 6, f"{maximum:.3g}")


class Layer31Viewer(QWidget):
    """Integrated biological-map viewer; it performs display processing only."""
    scenarioRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__(); self.data: Layer31ViewerData | None = None; self.mesh_result: BiologicalMeshResult | None = None
        self.viewer_state = BiologyViewerState()
        self.cad_bundle: CADSceneBundle | None = None
        self._mesh_generation = 0; self._mesh_workers: set[_MeshWorker] = set(); self._thread_pool = QThreadPool.globalInstance()
        self._mesh_cache: dict[tuple[Any, ...], CADSceneBundle] = {}
        self.crosshair: tuple[int, int, int] | None = None
        self._mesh_timer = QTimer(self); self._mesh_timer.setSingleShot(True); self._mesh_timer.setInterval(220); self._mesh_timer.timeout.connect(self._start_mesh_generation)

        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        header = QFrame(); header.setObjectName("card"); header_layout = QHBoxLayout(header)
        header_text = QVBoxLayout(); title = QLabel("Layer 3.1 — Spatial Radiobiological Evaluation"); title.setObjectName("sectionTitle")
        self.context_label = QLabel("Same validated anatomy, camera, masks, and crosshair with switchable biological fields.")
        self.context_label.setObjectName("sectionDescription"); self.context_label.setWordWrap(True)
        header_text.addWidget(title); header_text.addWidget(self.context_label); header_layout.addLayout(header_text, 1)
        self.context_status = QLabel("NOT LOADED"); self.context_status.setObjectName("statusPill"); header_layout.addWidget(self.context_status)
        layout.addWidget(header)

        self.hierarchy_label = QLabel(
            "1  MAP  →  2  WHOLE-TUMOUR RESULT  →  3  REGIONAL EXPLANATION"
        )
        self.hierarchy_label.setObjectName("sectionTitle")
        self.hierarchy_label.setAlignment(Qt.AlignCenter)
        self.hierarchy_label.setToolTip(
            "Interpret the spatial field first, then the whole-tumour SF/EUD, then the regional survivor-contribution decomposition."
        )
        layout.addWidget(self.hierarchy_label)

        workspace = QHBoxLayout(); layout.addLayout(workspace, 1)

        left = QFrame(); left.setObjectName("card"); left.setFixedWidth(255); left_layout = QVBoxLayout(left)
        left_title = QLabel("ANALYSIS CONTROLS"); left_title.setObjectName("sectionTitle"); left_layout.addWidget(left_title)
        left_layout.addWidget(QLabel("Displayed biological quantity"))
        self.field = QComboBox(); self.field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed); left_layout.addWidget(self.field)
        self.quantity_group = QButtonGroup(self); self.quantity_buttons: dict[str, QRadioButton] = {}
        for key, label in (("dose", "Physical dose"), ("bed", "Spatial BED"), ("eqd2", "Spatial EQD2"),
                           ("sf_log", "MLQ −log₁₀(SF)"), ("sf", "MLQ surviving fraction"), ("effect", "MLQ effect K")):
            button = QRadioButton(label); button.setEnabled(False); button.toggled.connect(lambda checked, value=key: self._quantity_button_changed(value, checked))
            self.quantity_group.addButton(button); self.quantity_buttons[key] = button; left_layout.addWidget(button)
        left_layout.addSpacing(8); left_layout.addWidget(QLabel("Tissue / anatomical focus"))
        self.roi = QComboBox(); left_layout.addWidget(self.roi)
        self.show_structures = QCheckBox("Structures"); self.show_structures.setChecked(True)
        self.show_warning = QCheckBox("High-dose LQ warning"); self.show_warning.setChecked(True)
        left_layout.addWidget(self.show_structures); left_layout.addWidget(self.show_warning)
        anatomy_title = QLabel("Anatomy visibility"); anatomy_title.setObjectName("sectionDescription"); left_layout.addWidget(anatomy_title)
        self.anatomy_checks: dict[str, QCheckBox] = {}
        for key, label in (("GTV", "GTV"), ("Vertices", "Vertices"), ("Valleys", "Valleys"), ("OAR", "OARs")):
            checkbox = QCheckBox(label); checkbox.setChecked(key in {"GTV", "Vertices", "Valleys"}); checkbox.toggled.connect(self._anatomy_changed)
            self.anatomy_checks[key] = checkbox; left_layout.addWidget(checkbox)
        left_layout.addSpacing(8); left_layout.addWidget(QLabel("Tumour sensitivity scenario"))
        scenario_row = QHBoxLayout(); self.scenario_buttons: dict[str, QPushButton] = {}
        for scenario in ("C1", "C2", "C3"):
            button = QPushButton(scenario); button.setCheckable(True); button.setEnabled(False)
            button.setToolTip("Select a standard tumour-sensitivity scenario and recalculate through the Layer 3.1 scientific service.")
            button.clicked.connect(lambda _checked=False, value=scenario: self._request_scenario(value))
            self.scenario_buttons[scenario] = button; scenario_row.addWidget(button)
        left_layout.addLayout(scenario_row)
        self.scenario_note = QLabel("Scenario changes are recalculated by the Layer 3.1 scientific service; no model calculation occurs in this viewer.")
        self.scenario_note.setObjectName("sectionDescription"); self.scenario_note.setWordWrap(True); left_layout.addWidget(self.scenario_note)
        left_layout.addSpacing(8); left_layout.addWidget(QLabel("Complete-volume colour scale"))
        self.range_mode = QComboBox(); self.range_mode.addItems(["Robust 2–98 percentile", "Full range", "Manual fixed range", "Percentile"])
        self.range_min = QLineEdit(); self.range_min.setPlaceholderText("minimum")
        self.range_max = QLineEdit(); self.range_max.setPlaceholderText("maximum")
        self.percentile_min = QLineEdit("5"); self.percentile_min.setPlaceholderText("P low")
        self.percentile_max = QLineEdit("95"); self.percentile_max.setPlaceholderText("P high")
        self.apply_range = QPushButton("Apply range")
        left_layout.addWidget(self.range_mode); range_row = QHBoxLayout(); range_row.addWidget(self.range_min); range_row.addWidget(self.range_max); left_layout.addLayout(range_row)
        percentile_row = QHBoxLayout(); percentile_row.addWidget(self.percentile_min); percentile_row.addWidget(self.percentile_max); left_layout.addLayout(percentile_row); left_layout.addWidget(self.apply_range)
        self.display_smoothing = QCheckBox("Display-only anatomical smoothing"); self.display_smoothing.setChecked(True)
        left_layout.addWidget(self.display_smoothing); smoothing = QLabel("Display smoothing: configured presentation surface\nAnalysis smoothing: NONE")
        smoothing.setObjectName("sectionDescription"); smoothing.setWordWrap(True); left_layout.addWidget(smoothing); left_layout.addStretch()
        workspace.addWidget(left)

        centre = QFrame(); centre.setObjectName("card"); centre_layout = QVBoxLayout(centre)
        map_heading = QLabel("1  MAP · PRIMARY SPATIAL OUTPUT"); map_heading.setObjectName("sectionTitle"); centre_layout.addWidget(map_heading)
        self.map_help = QLabel("Select a stored biological map."); self.map_help.setWordWrap(True); self.map_help.setObjectName("sectionDescription"); centre_layout.addWidget(self.map_help)
        self.tabs = QTabWidget(); centre_layout.addWidget(self.tabs, 1)
        planes = QWidget(); grid = QGridLayout(planes); self.canvases = {}
        self.sliders = {}
        for column, orientation in enumerate(("axial", "sagittal", "coronal")):
            canvas = BiologicalSliceCanvas(orientation); slider = QSlider(Qt.Horizontal)
            slider.valueChanged.connect(lambda value, view=orientation: self._slice_changed(view, value)); canvas.voxelSelected.connect(self._voxel_selected)
            self.canvases[orientation] = canvas; self.sliders[orientation] = slider
            tools = QHBoxLayout()
            for label, operation in (("−", lambda target=canvas: target.zoom_by(1/1.2)), ("+", lambda target=canvas: target.zoom_by(1.2)),
                                     ("↺", lambda target=canvas: target.rotate_by(-90)), ("↻", lambda target=canvas: target.rotate_by(90)),
                                     ("Fit", canvas.reset_view)):
                button = QPushButton(label); button.setToolTip("Zoom, rotate, or reset this plane"); button.clicked.connect(operation); tools.addWidget(button)
            tools.addStretch()
            grid.addWidget(canvas, 0, column); grid.addLayout(tools, 1, column); grid.addWidget(slider, 2, column)
        plane_help = QLabel("Interaction: mouse wheel zooms; left-drag pans; toolbar buttons rotate in 90° steps or reset to fit.")
        plane_help.setObjectName("sectionDescription"); grid.addWidget(plane_help, 3, 0, 1, 3)
        self.tabs.addTab(planes, "Linked axial / sagittal / coronal")
        spatial = QWidget(); spatial_layout = QVBoxLayout(spatial); row = QHBoxLayout()
        for label, orientation in (("Perspective", "perspective"), ("Axial", "axial"), ("Sagittal", "sagittal"), ("Coronal", "coronal")):
            button = QPushButton(label); button.clicked.connect(lambda _checked=False, value=orientation: self.scene.set_view(value)); row.addWidget(button)
        for label, operation in (("Zoom in", lambda: self.scene.zoom_by(0.82)), ("Zoom out", lambda: self.scene.zoom_by(1.22)),
                                 ("Rotate left", lambda: self.scene.rotate_by(-15)), ("Rotate right", lambda: self.scene.rotate_by(15))):
            button = QPushButton(label); button.clicked.connect(operation); row.addWidget(button)
        row.addStretch()
        self.export_button = QPushButton("Export anatomical STL + scalar VTP"); self.export_button.clicked.connect(self._export); row.addWidget(self.export_button)
        self.screenshot_button = QPushButton("Export view PNG"); self.screenshot_button.clicked.connect(self._export_screenshot); row.addWidget(self.screenshot_button)
        spatial_layout.addLayout(row)
        overlay_row = QHBoxLayout()
        self.cad_show_anatomy = QCheckBox("Anatomical CAD"); self.cad_show_anatomy.setChecked(True)
        self.cad_bed_overlay = QCheckBox("s-BED 3D overlay"); self.cad_bed_overlay.setChecked(True)
        self.cad_eqd2_overlay = QCheckBox("s-EQD2 3D overlay")
        self.cad_overlay_parameter = QComboBox(); self.cad_overlay_parameter.setMinimumWidth(220)
        self.show_vertex_centres = QCheckBox("Vertex centres"); self.show_vertex_centres.setChecked(True)
        self.show_neighbour_graph = QCheckBox("Layer 2.2 graph"); self.show_neighbour_graph.setChecked(False)
        overlay_row.addWidget(self.cad_show_anatomy); overlay_row.addWidget(self.cad_bed_overlay)
        overlay_row.addWidget(self.cad_eqd2_overlay); overlay_row.addWidget(QLabel("Tissue parameter"))
        overlay_row.addWidget(self.cad_overlay_parameter, 1); overlay_row.addWidget(self.show_vertex_centres); overlay_row.addWidget(self.show_neighbour_graph)
        spatial_layout.addLayout(overlay_row)
        mode_row = QHBoxLayout(); mode_row.addWidget(QLabel("3D mode"))
        self.cad_mode = QComboBox(); self.cad_mode.addItem("Biological surface map", "SURFACE"); self.cad_mode.addItem("Biological cutaway", "CUTAWAY"); self.cad_mode.addItem("Biological isosurfaces", "ISOSURFACE")
        self.cad_region = QComboBox(); self.cad_region.addItem("Whole GTV", "Region: Whole GTV"); self.cad_region.addItem("Vertices", "Region: Vertices"); self.cad_region.addItem("Valleys", "Region: Valleys"); self.cad_region.addItem("Neither", "Region: Other GTV")
        self.cad_physical_overlay = QCheckBox("Physical-dose 3D map")
        mode_row.addWidget(self.cad_mode); mode_row.addWidget(QLabel("Region focus")); mode_row.addWidget(self.cad_region); mode_row.addWidget(self.cad_physical_overlay); mode_row.addStretch()
        spatial_layout.addLayout(mode_row)
        advanced_row = QHBoxLayout(); advanced_row.addWidget(QLabel("Cut plane"))
        self.cut_axis = QComboBox(); self.cut_axis.addItems(["Axial", "Sagittal", "Coronal"])
        self.cut_offset = QSlider(Qt.Horizontal); self.cut_offset.setRange(0, 100); self.cut_offset.setValue(50); self.cut_offset.setMinimumWidth(160)
        self.cut_invert = QCheckBox("Invert")
        self.cut_azimuth = QSlider(Qt.Horizontal); self.cut_azimuth.setRange(-90, 90); self.cut_azimuth.setValue(0); self.cut_azimuth.setToolTip("Rotate clipping-plane normal in degrees")
        self.cut_elevation = QSlider(Qt.Horizontal); self.cut_elevation.setRange(-90, 90); self.cut_elevation.setValue(0); self.cut_elevation.setToolTip("Tilt clipping-plane normal in degrees")
        self.cut_reset = QPushButton("Reset cut")
        self.isosurface_thresholds = QLineEdit("P90"); self.isosurface_thresholds.setPlaceholderText("e.g. P75,P90 or 60,80 Gy")
        self.cad_contours = QCheckBox("Biological contour bands")
        self.biological_landscape = QPushButton("Biological Landscape"); self.biological_landscape.setToolTip("Display preset only; it does not modify Layer 3.1 calculations.")
        advanced_row.addWidget(self.cut_axis); advanced_row.addWidget(self.cut_offset); advanced_row.addWidget(self.cut_invert)
        advanced_row.addWidget(QLabel("Isosurfaces")); advanced_row.addWidget(self.isosurface_thresholds, 1); advanced_row.addWidget(self.cad_contours); advanced_row.addWidget(self.biological_landscape)
        spatial_layout.addLayout(advanced_row)
        cut_rotation_row = QHBoxLayout(); cut_rotation_row.addWidget(QLabel("Cut rotation  azimuth")); cut_rotation_row.addWidget(self.cut_azimuth, 1)
        cut_rotation_row.addWidget(QLabel("elevation")); cut_rotation_row.addWidget(self.cut_elevation, 1); cut_rotation_row.addWidget(self.cut_reset)
        spatial_layout.addLayout(cut_rotation_row)
        opacity_row = QHBoxLayout(); opacity_row.addWidget(QLabel("Opacity  GTV"))
        self.gtv_opacity = QSlider(Qt.Horizontal); self.gtv_opacity.setRange(5, 100); self.gtv_opacity.setValue(96)
        self.oar_opacity = QSlider(Qt.Horizontal); self.oar_opacity.setRange(0, 100); self.oar_opacity.setValue(25)
        self.iso_opacity = QSlider(Qt.Horizontal); self.iso_opacity.setRange(5, 100); self.iso_opacity.setValue(45)
        opacity_row.addWidget(self.gtv_opacity); opacity_row.addWidget(QLabel("OAR")); opacity_row.addWidget(self.oar_opacity)
        opacity_row.addWidget(QLabel("Isosurface")); opacity_row.addWidget(self.iso_opacity); spatial_layout.addLayout(opacity_row)
        metric_row = QHBoxLayout(); self.cad_metric_cards: dict[str, QLabel] = {}
        for key, title_text in (("mean", "MEAN"), ("max", "MAX"), ("d95", "D95"), ("min", "MIN")):
            card = QLabel(f"{title_text}\n—"); card.setObjectName("metricCard"); card.setAlignment(Qt.AlignCenter); card.setMinimumHeight(54)
            self.cad_metric_cards[key] = card; metric_row.addWidget(card, 1)
        spatial_layout.addLayout(metric_row)
        self.cad_legend = QLabel("Anatomical surfaces use validated Layer 1 masks in DICOM patient LPS. Select s-BED or s-EQD2 to map the stored field onto the smoothed GTV surface.")
        self.cad_legend.setObjectName("sectionDescription"); self.cad_legend.setWordWrap(True); spatial_layout.addWidget(self.cad_legend)
        self.colour_bar = BiologyColorBar(); spatial_layout.addWidget(self.colour_bar)
        # Qt3D 6.10 on macOS 15 can crash below Python in Apple's Metal render
        # pipeline. Use the same authoritative mesh bundle with a raster Qt
        # viewport on macOS; other platforms retain the native Qt3D viewport.
        self.scene = SoftwareBiologicalScene3D() if sys.platform == "darwin" else BiologicalScene3D()
        spatial_layout.addWidget(self.scene, 1)
        self.mesh_status = QLabel("Select a stored map and ROI."); self.mesh_status.setWordWrap(True); spatial_layout.addWidget(self.mesh_status)
        cad_help = QLabel("Interaction: left-drag rotates; middle-drag pans; mouse wheel zooms. GTV is gold, vertices cyan, valleys violet, configured OARs magenta, and the selected s-BED/s-EQD2 field is shown as ten quantitative surface bands.")
        cad_help.setObjectName("sectionDescription"); spatial_layout.addWidget(cad_help)
        self.tabs.addTab(spatial, "Interactive 3D GTV / structure")
        comparison = QWidget(); comparison_layout = QHBoxLayout(comparison)
        self.comparison_left = QLabel(); self.comparison_right = QLabel()
        for widget in (self.comparison_left, self.comparison_right):
            widget.setAlignment(Qt.AlignCenter); widget.setWordWrap(True); widget.setObjectName("metricCard"); widget.setMinimumHeight(280); comparison_layout.addWidget(widget, 1)
        self.tabs.addTab(comparison, "Compare LRT vs LRT+cERT")
        workspace.addWidget(centre, 1)

        right = QFrame(); right.setObjectName("card"); right.setFixedWidth(300); right_layout = QVBoxLayout(right)
        heading = QLabel("2  WHOLE-TUMOUR RESULT"); heading.setObjectName("sectionTitle"); right_layout.addWidget(heading)
        primary_note = QLabel("The major 3.1B outputs are mean tumour surviving fraction and tumour EUD. The map explains their spatial origin.")
        primary_note.setObjectName("sectionDescription"); primary_note.setWordWrap(True); right_layout.addWidget(primary_note)
        self.primary_sf = QLabel("MEAN TUMOUR SF\n—"); self.primary_sf.setObjectName("metricCard"); self.primary_sf.setAlignment(Qt.AlignCenter); self.primary_sf.setMinimumHeight(82)
        self.primary_eud = QLabel("MLQ TUMOUR EUD\n—"); self.primary_eud.setObjectName("metricCard"); self.primary_eud.setAlignment(Qt.AlignCenter); self.primary_eud.setMinimumHeight(82)
        right_layout.addWidget(self.primary_sf); right_layout.addWidget(self.primary_eud)
        map_detail_heading = QLabel("Selected map interpretation"); map_detail_heading.setObjectName("sectionTitle"); right_layout.addWidget(map_detail_heading)
        self.summary_title = QLabel("No map loaded"); self.summary_title.setObjectName("metricTitle"); self.summary_title.setWordWrap(True); right_layout.addWidget(self.summary_title)
        self.summary_equation = QLabel("—"); self.summary_equation.setWordWrap(True); right_layout.addWidget(self.summary_equation)
        self.summary_details = QLabel("—"); self.summary_details.setObjectName("sectionDescription"); self.summary_details.setWordWrap(True); right_layout.addWidget(self.summary_details)
        voxel_title = QLabel("Voxel under crosshair"); voxel_title.setObjectName("sectionTitle"); right_layout.addWidget(voxel_title)
        self.voxel_chain = QLabel("Double-click a 2D view to inspect one voxel."); self.voxel_chain.setWordWrap(True); self.voxel_chain.setTextInteractionFlags(Qt.TextSelectableByMouse); right_layout.addWidget(self.voxel_chain)
        self.warning_summary = QLabel(""); self.warning_summary.setWordWrap(True); self.warning_summary.setObjectName("warningBanner"); right_layout.addWidget(self.warning_summary); right_layout.addStretch()
        workspace.addWidget(right)

        regional = QFrame(); regional.setObjectName("card"); regional_layout = QVBoxLayout(regional)
        self.regional_title = QLabel("3  REGIONAL EXPLANATION · WHO DRIVES RESIDUAL TUMOUR SURVIVAL?")
        self.regional_title.setObjectName("sectionTitle"); regional_layout.addWidget(self.regional_title)
        contribution_note = QLabel("Primary regional visual · 100% residual-survival contribution bar. Select a segment to focus the linked vertex, valley, or other-GTV mask in 2D and 3D.")
        contribution_note.setObjectName("sectionDescription"); contribution_note.setWordWrap(True); regional_layout.addWidget(contribution_note)
        self.contribution_bar = SurvivalContributionBar(); self.contribution_bar.selected.connect(self._focus_region); regional_layout.addWidget(self.contribution_bar)
        cards = QHBoxLayout(); self.regional_cards: dict[str, RegionalResultCard] = {}
        for region, title_text in (("H", "VERTICES"), ("V", "VALLEYS"), ("O", "OTHER GTV")):
            card = RegionalResultCard(region, title_text); card.selected.connect(self._focus_region); self.regional_cards[region] = card; cards.addWidget(card)
        self.whole_tumour_card = QLabel("WHOLE TUMOUR\nMean SF  —\nEUD  —"); self.whole_tumour_card.setObjectName("metricCard"); self.whole_tumour_card.setAlignment(Qt.AlignCenter); cards.addWidget(self.whole_tumour_card)
        regional_layout.addLayout(cards)
        self.distribution = SurvivalDistributionCanvas(); regional_layout.addWidget(self.distribution)
        regional_note = QLabel("Click a regional card or contribution segment to focus its validated mask. Smoothing is presentation-only; all metrics use raw stored voxel fields.")
        regional_note.setObjectName("sectionDescription"); regional_note.setWordWrap(True); regional_layout.addWidget(regional_note)
        layout.addWidget(regional)

        self.field.currentIndexChanged.connect(self._selection_changed); self.roi.currentIndexChanged.connect(self._selection_changed)
        self.show_structures.toggled.connect(self._refresh_views); self.show_warning.toggled.connect(self._refresh_views)
        self.apply_range.clicked.connect(self._selection_changed)
        self.range_mode.currentIndexChanged.connect(self._selection_changed)
        self.tabs.currentChanged.connect(self._viewer_tab_changed)
        self.display_smoothing.toggled.connect(self._cad_controls_changed)
        self.cad_show_anatomy.toggled.connect(self._cad_controls_changed)
        self.cad_bed_overlay.toggled.connect(lambda checked: self._cad_overlay_toggled("bed", checked))
        self.cad_eqd2_overlay.toggled.connect(lambda checked: self._cad_overlay_toggled("eqd2", checked))
        self.cad_overlay_parameter.currentIndexChanged.connect(self._cad_controls_changed)
        self.cad_physical_overlay.toggled.connect(self._cad_physical_toggled)
        self.cad_mode.currentIndexChanged.connect(self._cad_mode_changed)
        self.cad_region.currentIndexChanged.connect(self._cad_region_changed)
        self.cut_axis.currentIndexChanged.connect(self._cad_controls_changed)
        self.cut_offset.valueChanged.connect(self._cad_controls_changed)
        self.cut_invert.toggled.connect(self._cad_controls_changed)
        self.cut_azimuth.valueChanged.connect(self._cad_controls_changed)
        self.cut_elevation.valueChanged.connect(self._cad_controls_changed)
        self.cut_reset.clicked.connect(self._reset_cut_plane)
        self.isosurface_thresholds.editingFinished.connect(self._cad_controls_changed)
        self.cad_contours.toggled.connect(self._cad_controls_changed)
        self.show_vertex_centres.toggled.connect(self._cad_controls_changed)
        self.show_neighbour_graph.toggled.connect(self._cad_controls_changed)
        self.gtv_opacity.valueChanged.connect(self._cad_opacity_changed); self.oar_opacity.valueChanged.connect(self._cad_opacity_changed); self.iso_opacity.valueChanged.connect(self._cad_opacity_changed)
        self.biological_landscape.clicked.connect(self._apply_landscape_preset)
        self.scene.pointPicked.connect(self._cad_point_picked)
        self._cad_mode_changed()

    def set_data(self, data: Layer31ViewerData) -> None:
        self.data = data; self.field.clear(); self.roi.clear()
        preferred_order = [
            "physical_course_dose_gy", "negative_log10_survival_MLQ", "voxel_survival_MLQ", "course_effect_MLQ",
            *[key for key in data.fields if "BED" in key], *[key for key in data.fields if "EQD2" in key], "LQ_high_dose_warning_mask",
        ]
        seen: set[str] = set()
        for key in preferred_order:
            if key in seen or key not in data.field_metadata: continue
            seen.add(key); item = data.field_metadata[key]; self.field.addItem(f"{item['label']} · {item['units']}", key)
        for key, item in data.field_metadata.items():
            if key not in seen: self.field.addItem(f"{item['label']} · {item['units']}", key)
        for name in data.masks: self.roi.addItem(name, name)
        self._configure_cad_overlays()
        has_oars = any(name.startswith("OAR:") for name in data.masks)
        self.anatomy_checks["OAR"].blockSignals(True)
        self.anatomy_checks["OAR"].setEnabled(has_oars)
        self.anatomy_checks["OAR"].setChecked(has_oars)
        self.anatomy_checks["OAR"].blockSignals(False)
        self.show_vertex_centres.setEnabled(bool(data.vertex_centres_lps_mm))
        self.show_neighbour_graph.setEnabled(bool(data.graph_edges_lps_mm))
        if not data.vertex_centres_lps_mm: self.show_vertex_centres.setChecked(False)
        if not data.graph_edges_lps_mm: self.show_neighbour_graph.setChecked(False)
        shape = next(iter(data.fields.values())).shape
        gtv = data.masks.get("Region: Whole GTV")
        if gtv is not None and np.any(gtv):
            coordinates = np.nonzero(gtv); self.crosshair = tuple(int(round(float(np.mean(axis)))) for axis in coordinates)
        else:
            self.crosshair = tuple(int(value // 2) for value in shape)
        for orientation, axis in (("axial", 0), ("sagittal", 2), ("coronal", 1)):
            self.sliders[orientation].blockSignals(True); self.sliders[orientation].setRange(0, shape[axis]-1); self.sliders[orientation].setValue(shape[axis]//2); self.sliders[orientation].blockSignals(False)
        self._configure_quantity_buttons()
        branch = data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        scenario = str(branch.get("scenario_id") or "")
        for key, button in self.scenario_buttons.items():
            button.setChecked(key == scenario); button.setEnabled(True)
        context = data.result.get("treatment_context") or {}
        history = data.result.get("fraction_history") or {}
        treatment = context.get("treatment_delivery_mode") or context.get("treatment_context") or "Resolved treatment"
        self.context_label.setText(
            f"Treatment: {treatment} · {history.get('number_of_biological_fraction_events', 0)} biological fraction event(s) · "
            "same validated anatomy and navigation across every field."
        )
        self.context_status.setText(str(branch.get("status") or branch.get("calculation_status") or "LOADED").upper())
        self._update_regional_results(); self.distribution.set_data(data); self._update_comparison_state()
        self._selection_changed()

    def _request_scenario(self, scenario: str) -> None:
        if self.data is None: return
        current = str((self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}).get("scenario_id") or "")
        if scenario == current: return
        for button in self.scenario_buttons.values(): button.setEnabled(False)
        self.scenario_note.setText(f"RECALCULATING {scenario} through the Layer 3.1 scientific service…")
        self.scenarioRequested.emit(scenario)

    def _configure_quantity_buttons(self) -> None:
        if self.data is None: return
        mapping = {
            "dose": "physical_course_dose_gy",
            "sf_log": "negative_log10_survival_MLQ",
            "sf": "voxel_survival_MLQ",
            "effect": "course_effect_MLQ",
        }
        mapping["bed"] = next((key for key in self.data.fields if "BED" in key), "")
        mapping["eqd2"] = next((key for key in self.data.fields if "EQD2" in key), "")
        self._quantity_fields = mapping
        for key, button in self.quantity_buttons.items():
            available = bool(mapping.get(key) and mapping[key] in self.data.fields); button.setEnabled(available)
        default_key = "sf_log" if mapping.get("sf_log") in self.data.fields else "bed" if mapping.get("bed") in self.data.fields else "dose"
        if mapping.get(default_key) in self.data.fields:
            self.field.setCurrentIndex(max(self.field.findData(mapping[default_key]), 0)); self.quantity_buttons[default_key].setChecked(True)

    def _quantity_button_changed(self, key: str, checked: bool) -> None:
        if not checked or not hasattr(self, "_quantity_fields"): return
        field = self._quantity_fields.get(key); index = self.field.findData(field)
        if index >= 0: self.field.setCurrentIndex(index)

    def _sync_quantity_button(self, field: str) -> None:
        if not hasattr(self, "_quantity_fields"): return
        for key, mapped in self._quantity_fields.items():
            if mapped == field:
                self.quantity_buttons[key].blockSignals(True); self.quantity_buttons[key].setChecked(True); self.quantity_buttons[key].blockSignals(False); break

    def _slice_changed(self, orientation: str, value: int) -> None:
        if self.data is None: return
        current = list(self.crosshair or (0, 0, 0)); axis = {"axial": 0, "coronal": 1, "sagittal": 2}[orientation]
        current[axis] = int(value); self.crosshair = tuple(current); self._refresh_views(); self._update_voxel_chain()

    def _voxel_selected(self, z_index: int, y_index: int, x_index: int) -> None:
        self.crosshair = (z_index, y_index, x_index)
        for orientation, value in (("axial", z_index), ("coronal", y_index), ("sagittal", x_index)):
            self.sliders[orientation].blockSignals(True); self.sliders[orientation].setValue(value); self.sliders[orientation].blockSignals(False)
        self._refresh_views(); self._update_voxel_chain()

    def _anatomy_changed(self, _checked: bool = False) -> None:
        if self.data is None: return
        current = str(self.roi.currentData() or "")
        categories = {
            "GTV": lambda name: "GTV" in name and "Other" not in name,
            "Vertices": lambda name: "VTV_H" in name or "Vertices" in name,
            "Valleys": lambda name: "VTV_L" in name or "Valleys" in name,
            "OAR": lambda name: name.startswith("OAR:"),
        }
        allowed = [name for name in self.data.masks if any(self.anatomy_checks[key].isChecked() and predicate(name) for key, predicate in categories.items())]
        if current not in allowed and allowed:
            index = self.roi.findData(allowed[0]);
            if index >= 0: self.roi.setCurrentIndex(index)
        self.show_structures.setChecked(bool(allowed)); self._refresh_views()
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _configure_cad_overlays(self) -> None:
        """Expose stored BED/EQD2 field pairs without creating GUI calculations."""
        if self.data is None:
            return
        grouped: dict[float, dict[str, str]] = {}
        for field_id, meta in self.data.field_metadata.items():
            alpha_beta = meta.get("alpha_beta_gy")
            if alpha_beta is None:
                continue
            record = grouped.setdefault(float(alpha_beta), {})
            if "EQD2" in field_id:
                record["eqd2"] = field_id
            elif "BED" in field_id:
                record["bed"] = field_id
        tumour_alpha_beta = next((
            float(item["assignment"]["alpha_beta_gy"])
            for item in ((self.data.result.get("layer3_1a_conventional_lq") or {}).get("roi_summaries") or [])
            if (item.get("assignment") or {}).get("canonical_role") in {"GTV", "VTV_H", "VTV_L"}
            and (item.get("assignment") or {}).get("alpha_beta_gy") is not None
        ), None)
        self.cad_overlay_parameter.blockSignals(True); self.cad_overlay_parameter.clear()
        selected_index = 0
        for index, alpha_beta in enumerate(sorted(grouped)):
            record = {**grouped[alpha_beta], "alpha_beta_gy": alpha_beta}
            self.cad_overlay_parameter.addItem(f"α/β {alpha_beta:g} Gy", record)
            if tumour_alpha_beta is not None and np.isclose(alpha_beta, tumour_alpha_beta):
                selected_index = index
        if self.cad_overlay_parameter.count():
            self.cad_overlay_parameter.setCurrentIndex(selected_index)
        self.cad_overlay_parameter.blockSignals(False)
        current = self.cad_overlay_parameter.currentData() or {}
        self.cad_bed_overlay.setEnabled(bool(current.get("bed")))
        self.cad_eqd2_overlay.setEnabled(bool(current.get("eqd2")))
        if not self.cad_bed_overlay.isEnabled():
            self.cad_bed_overlay.setChecked(False)
        if not self.cad_eqd2_overlay.isEnabled():
            self.cad_eqd2_overlay.setChecked(False)
        self._update_cad_metric_cards(self._cad_overlay_field())

    def _cad_overlay_toggled(self, kind: str, checked: bool) -> None:
        """Keep BED and EQD2 overlays individually switchable but non-overlapping."""
        if getattr(self, "_cad_toggle_guard", False):
            return
        self._cad_toggle_guard = True
        try:
            if checked and kind == "bed":
                self.cad_eqd2_overlay.setChecked(False)
            elif checked and kind == "eqd2":
                self.cad_bed_overlay.setChecked(False)
            if checked:
                self.cad_physical_overlay.setChecked(False)
        finally:
            self._cad_toggle_guard = False
        if checked:
            selected = self._cad_overlay_field(); index = self.field.findData(selected)
            if index >= 0: self.field.setCurrentIndex(index)
        self._cad_controls_changed()

    def _cad_physical_toggled(self, checked: bool) -> None:
        if getattr(self, "_cad_toggle_guard", False): return
        self._cad_toggle_guard = True
        try:
            if checked:
                self.cad_bed_overlay.setChecked(False); self.cad_eqd2_overlay.setChecked(False)
        finally:
            self._cad_toggle_guard = False
        if checked:
            index = self.field.findData("physical_course_dose_gy")
            if index >= 0: self.field.setCurrentIndex(index)
        self._cad_controls_changed()

    def _cad_overlay_field(self) -> str | None:
        if self.cad_physical_overlay.isChecked():
            return "physical_course_dose_gy"
        record = self.cad_overlay_parameter.currentData()
        if not isinstance(record, dict):
            return None
        if self.cad_bed_overlay.isChecked():
            return str(record.get("bed")) if record.get("bed") else None
        if self.cad_eqd2_overlay.isChecked():
            return str(record.get("eqd2")) if record.get("eqd2") else None
        return None

    def _cad_mode_changed(self, _index: int = -1) -> None:
        mode = str(self.cad_mode.currentData() or "SURFACE")
        cutaway = mode == "CUTAWAY"; iso = mode == "ISOSURFACE"
        for widget in (self.cut_axis, self.cut_offset, self.cut_invert, self.cut_azimuth, self.cut_elevation, self.cut_reset): widget.setEnabled(cutaway)
        self.isosurface_thresholds.setEnabled(iso)
        self.viewer_state.display_mode = mode
        self._cad_controls_changed()

    def _reset_cut_plane(self) -> None:
        self.cut_offset.setValue(50); self.cut_azimuth.setValue(0); self.cut_elevation.setValue(0); self.cut_invert.setChecked(False)
        self._cad_controls_changed()

    def _cad_region_changed(self, _index: int = -1) -> None:
        name = str(self.cad_region.currentData() or "Region: Whole GTV")
        self.viewer_state.active_region = name
        if self.data is not None:
            index = self.roi.findData(name)
            if index >= 0: self.roi.setCurrentIndex(index)
        if self.cad_bundle is not None: self.scene.set_bundle(self.cad_bundle, name)

    def _cad_opacity_changed(self, _value: int = 0) -> None:
        self.viewer_state.gtv_opacity = self.gtv_opacity.value() / 100.0
        self.viewer_state.oar_opacity = self.oar_opacity.value() / 100.0
        self.viewer_state.isosurface_opacity = self.iso_opacity.value() / 100.0
        if self.cad_bundle is None: return
        self.cad_bundle.gtv_opacity = self.viewer_state.gtv_opacity
        self.cad_bundle.oar_opacity = self.viewer_state.oar_opacity
        self.cad_bundle.isosurface_opacity = self.viewer_state.isosurface_opacity
        self.scene.set_bundle(self.cad_bundle, str(self.cad_region.currentData() or ""))

    def _apply_landscape_preset(self) -> None:
        self.cad_show_anatomy.setChecked(True); self.anatomy_checks["GTV"].setChecked(True)
        self.anatomy_checks["Vertices"].setChecked(True); self.anatomy_checks["OAR"].setChecked(True)
        self.show_vertex_centres.setChecked(bool(self.data and self.data.vertex_centres_lps_mm))
        self.cad_bed_overlay.setChecked(True); self.cad_mode.setCurrentIndex(self.cad_mode.findData("ISOSURFACE"))
        self.isosurface_thresholds.setText("P90"); self.cad_contours.setChecked(True); self._cad_controls_changed()

    def _resolved_isosurface_thresholds(self) -> tuple[float, ...]:
        if self.data is None: return ()
        field_id = self._cad_overlay_field(); gtv = self.data.masks.get("Region: Whole GTV")
        if not field_id or gtv is None: return ()
        values = np.asarray(self.data.fields[field_id], dtype=float)[np.asarray(gtv, dtype=bool)]
        values = values[np.isfinite(values)]
        if not values.size: return ()
        thresholds: list[float] = []
        for token in self.isosurface_thresholds.text().split(",")[:4]:
            value = token.strip().upper()
            try:
                thresholds.append(float(np.percentile(values, float(value[1:]))) if value.startswith("P") else float(value.split()[0]))
            except ValueError:
                continue
        return tuple(sorted(set(item for item in thresholds if np.isfinite(item))))

    def _cad_point_picked(self, x_lps: float, y_lps: float, z_lps: float) -> None:
        if self.data is None: return
        point = (float(x_lps), float(y_lps), float(z_lps)); indices = world_to_voxel_lps(np.asarray([point]), self.data.geometry)[0]
        if not np.isfinite(indices).all(): return
        shape = np.asarray(next(iter(self.data.fields.values())).shape)
        voxel = np.clip(np.rint(indices).astype(int), 0, shape - 1)
        self.viewer_state.selected_world_position_lps = point
        self.scene.selected_world_position = np.asarray(point); self.scene.set_bundle(self.cad_bundle, str(self.cad_region.currentData() or "")) if self.cad_bundle else None
        self._voxel_selected(int(voxel[0]), int(voxel[1]), int(voxel[2]))

    def _cad_mask_names(self) -> tuple[str, ...]:
        if self.data is None or not self.cad_show_anatomy.isChecked():
            return ()
        candidates: list[str] = []
        if self.anatomy_checks["GTV"].isChecked(): candidates.append("Region: Whole GTV")
        if self.anatomy_checks["Vertices"].isChecked(): candidates.append("Region: Vertices")
        if self.anatomy_checks["Valleys"].isChecked(): candidates.append("Region: Valleys")
        if self.anatomy_checks["OAR"].isChecked():
            candidates.extend(
                name for name in self.data.masks
                if name.startswith("OAR:") and name.split(":", 1)[-1].strip().upper() not in {"BODY", "EXTERNAL", "BODY-PTV"}
            )
        return tuple(name for name in dict.fromkeys(candidates) if name in self.data.masks and np.asarray(self.data.masks[name]).any())

    def _cad_smoothing(self) -> dict[str, Any]:
        if not self.display_smoothing.isChecked():
            return {"method": "none", "iterations": 0, "lambda": 0.0, "mu": 0.0}
        if self.data is None:
            return {"method": "taubin_non_shrinking", "iterations": 12, "lambda": 0.25, "mu": -0.27}
        return dict((self.data.result.get("visualisation") or {}).get("smoothing") or {
            "method": "taubin_non_shrinking", "iterations": 12, "lambda": 0.25, "mu": -0.27,
        })

    def _cad_controls_changed(self, _value: Any = None) -> None:
        if self.data is None:
            return
        record = self.cad_overlay_parameter.currentData() or {}
        bed_available = bool(record.get("bed")); eqd2_available = bool(record.get("eqd2"))
        self.cad_bed_overlay.setEnabled(bed_available)
        self.cad_eqd2_overlay.setEnabled(eqd2_available)
        # Never retain a checked state for a field that is absent at the newly
        # selected tissue parameter.  Block signals because this method already
        # owns the single required CAD refresh.
        if not bed_available and self.cad_bed_overlay.isChecked():
            self.cad_bed_overlay.blockSignals(True); self.cad_bed_overlay.setChecked(False); self.cad_bed_overlay.blockSignals(False)
        if not eqd2_available and self.cad_eqd2_overlay.isChecked():
            self.cad_eqd2_overlay.blockSignals(True); self.cad_eqd2_overlay.setChecked(False); self.cad_eqd2_overlay.blockSignals(False)
        overlay_field = self._cad_overlay_field()
        self._update_cad_metric_cards(overlay_field)
        if overlay_field:
            index = self.field.findData(overlay_field)
            if index >= 0 and self.field.currentIndex() != index: self.field.setCurrentIndex(index)
        if self.tabs.currentIndex() == 1:
            self._mesh_timer.start(25)

    def _update_cad_metric_cards(self, field_id: str | None) -> None:
        if self.data is None or not field_id or field_id not in self.data.field_metadata:
            for key, card in self.cad_metric_cards.items(): card.setText(f"{key.upper()}\nNOT STORED")
            return
        meta = self.data.field_metadata[field_id]; alpha_beta = meta.get("alpha_beta_gy")
        kind = "eqd2" if "EQD2" in field_id else "bed" if "BED" in field_id else None
        summary = next((
            item for item in ((self.data.result.get("layer3_1a_conventional_lq") or {}).get("roi_summaries") or [])
            if (item.get("assignment") or {}).get("canonical_role") == "GTV"
            and alpha_beta is not None
            and np.isclose(float((item.get("assignment") or {}).get("alpha_beta_gy", np.nan)), float(alpha_beta))
        ), None)
        for key, card in self.cad_metric_cards.items():
            title_text = key.upper(); metric = f"{kind}_{key}" if kind else ""
            value = (summary.get("metrics") or {}).get(metric) if summary else None
            card.setText(f"{title_text}\n{float(value):.5g} {meta['units']}" if value is not None else f"{title_text}\nNOT STORED")

    def _focus_region(self, region_id: str) -> None:
        names = {"H": "Region: Vertices", "V": "Region: Valleys", "O": "Region: Other GTV"}
        index = self.roi.findData(names.get(region_id, ""))
        if index >= 0:
            self.show_structures.setChecked(True); self.roi.setCurrentIndex(index)

    def _update_regional_results(self) -> None:
        if self.data is None: return
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        records = list((branch.get("regional_survival") or {}).get("records", [])); by_region = {str(item.get("region_id")): item for item in records}
        for region, card in self.regional_cards.items(): card.set_record(by_region.get(region))
        self.contribution_bar.set_records(records)
        mean_sf = branch.get("mean_tumour_survival_fraction"); eud = branch.get("tumour_eud_gy")
        sf_text = f"{float(mean_sf):.5g}" if mean_sf is not None else "—"; eud_text = f"{float(eud):.4g} Gy" if eud is not None else "NOT APPLICABLE"
        self.primary_sf.setText(f"MEAN TUMOUR SF\n{sf_text}"); self.primary_eud.setText(f"MLQ TUMOUR EUD\n{eud_text}")
        self.whole_tumour_card.setText(f"WHOLE TUMOUR\nMean SF  {sf_text}\nEUD  {eud_text}")

    def _update_comparison_state(self) -> None:
        if self.data is None: return
        context = self.data.result.get("treatment_context") or {}; branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}
        mean_sf = branch.get("mean_tumour_survival_fraction"); eud = branch.get("tumour_eud_gy")
        self.comparison_left.setText(
            "CURRENT VALIDATED COURSE\n\n"
            f"{context.get('treatment_delivery_mode') or context.get('dose_context') or 'Configured treatment'}\n\n"
            f"Mean SF: {float(mean_sf):.5g}\n" if mean_sf is not None else "CURRENT VALIDATED COURSE\n\nMean SF: —\n"
        )
        if eud is not None: self.comparison_left.setText(self.comparison_left.text() + f"EUD: {float(eud):.4g} Gy")
        self.comparison_right.setText(
            "PAIRED COMPARISON COURSE\n\nNOT CONFIGURED\n\n"
            "Direct comparison requires a second hash-verified Layer 3.1 field set on the same validated geometry. ASCEND does not duplicate or infer one course."
        )

    def _refresh_views(self, _value: Any = None) -> None:
        if self.data is None: return
        field, roi = str(self.field.currentData()), str(self.roi.currentData())
        visible = self._visible_mask_names()
        for orientation, canvas in self.canvases.items():
            canvas.set_view(self.data, field, self.sliders[orientation].value(), roi, self.show_structures.isChecked(), self.show_warning.isChecked(), self._scalar_range(), self.crosshair, visible)

    def _visible_mask_names(self) -> list[str]:
        if self.data is None: return []
        selected: list[str] = []
        preferred = {
            "GTV": ["Region: Whole GTV"], "Vertices": ["Region: Vertices"],
            "Valleys": ["Region: Valleys"], "OAR": [name for name in self.data.masks if name.startswith("OAR:")],
        }
        for category, names in preferred.items():
            if self.anatomy_checks[category].isChecked(): selected.extend(name for name in names if name in self.data.masks)
        return selected

    def _scalar_range(self) -> tuple[float, float] | None:
        if self.data is None or self.field.currentData() is None: return None
        field_id = str(self.field.currentData()); contract = self.data.spatial_fields.get(field_id)
        if contract is None:
            return tuple(self.data.field_metadata[field_id]["display_range"])
        mode = self.range_mode.currentIndex(); manual = None; percentiles = (2.0, 98.0)
        if mode == 2:
            try: manual = (float(self.range_min.text()), float(self.range_max.text()))
            except ValueError: return tuple(self.data.field_metadata[field_id]["display_range"])
            controller_mode = "MANUAL"
        elif mode == 1:
            controller_mode = "FULL RANGE"
        elif mode == 3:
            try: percentiles = (float(self.percentile_min.text()), float(self.percentile_max.text()))
            except ValueError: return tuple(self.data.field_metadata[field_id]["display_range"])
            if not 0.0 <= percentiles[0] < percentiles[1] <= 100.0:
                return tuple(self.data.field_metadata[field_id]["display_range"])
            controller_mode = "PERCENTILE"
        else:
            controller_mode = "ROBUST"
        roi = self.data.masks.get(str(self.roi.currentData() or ""))
        try:
            return BiologyColorScaleController(controller_mode, percentiles).resolve(contract, roi_mask=roi, manual=manual)
        except ValueError:
            return tuple(self.data.field_metadata[field_id]["display_range"])

    def _selection_changed(self, _index: int = -1) -> None:
        if self.data is None or self.field.currentData() is None or self.roi.currentData() is None: return
        self._refresh_views(); field = str(self.field.currentData()); meta = self.data.field_metadata[field]
        self._sync_quantity_button(field)
        low, high = self._scalar_range() or tuple(meta["display_range"])
        actual_range = tuple(meta["display_range"])
        self.colour_bar.set_scale(meta, (float(low), float(high)), (float(actual_range[0]), float(actual_range[1])))
        self.map_help.setText(
            f"{meta['category']}  |  {meta['label']}  |  {meta['equation']}\n"
            f"{meta['interpretation']}  Complete-field colour range: {low:.5g} to {high:.5g} {meta['units']}."
        )
        self.summary_title.setText(str(meta["label"]).upper()); self.summary_equation.setText(f"Equation\n{meta['equation']}")
        branch = self.data.result.get("layer3_1b_high_dose_sfrt_response") or {}; parameters = branch.get("model_parameters") or {}
        parameter_lines = []
        for key, label, units in (("alpha_beta_gy", "α/β", "Gy"), ("alpha_per_gy", "α", "Gy⁻¹"), ("beta_per_gy2", "β", "Gy⁻²"),
                                  ("delta_per_gy", "δ", "Gy⁻¹"), ("repair_half_time", "Repair half-time", str(parameters.get("time_unit") or ""))):
            if parameters.get(key) is not None: parameter_lines.append(f"{label}: {parameters[key]} {units}".rstrip())
        self.summary_details.setText(
            f"Units: {meta['units']}\nComplete-volume range: {low:.5g} – {high:.5g}\n"
            f"Model: {branch.get('formalism_id') or 'LQ reference'}\nScenario: {branch.get('scenario_id') or 'Not applicable'}\n"
            + "\n".join(parameter_lines) + f"\n\n{meta['interpretation']}"
        )
        warnings = list(branch.get("warnings", [])); self.warning_summary.setText("WARNINGS\n" + "\n".join(warnings) if warnings else "No stored model warning for this view.")
        self._update_voxel_chain()
        if self.tabs.currentIndex() == 1:
            if getattr(self, "cad_bundle", None) is not None:
                self.scene.set_bundle(self.cad_bundle, str(self.cad_region.currentData() or ""))

    def _update_voxel_chain(self) -> None:
        if self.data is None or self.crosshair is None or self.field.currentData() is None: return
        z_index, y_index, x_index = self.crosshair; point = (z_index, y_index, x_index)
        field = str(self.field.currentData()); displayed = float(self.data.fields[field][point])
        physical = self.data.fields.get("physical_course_dose_gy"); survival = self.data.fields.get("voxel_survival_MLQ")
        effect = self.data.fields.get("course_effect_MLQ")
        region = next((name.replace("Region: ", "") for name in ("Region: Vertices", "Region: Valleys", "Region: Other GTV", "Region: Whole GTV") if name in self.data.masks and self.data.masks[name][point]), "Outside selected tumour regions")
        lps = voxel_to_world_lps(np.asarray([[z_index, y_index, x_index]], dtype=float), self.data.geometry)[0]
        lines = [
            f"Position LPS: x {lps[0]:.3f}, y {lps[1]:.3f}, z {lps[2]:.3f} mm",
            f"Grid index: z {z_index}, y {y_index}, x {x_index}",
            f"Displayed: {displayed:.6g} {self.data.field_metadata[field]['units']}",
        ]
        if physical is not None: lines.append(f"Physical course dose: {float(physical[point]):.6g} Gy")
        alpha_beta = self.data.field_metadata[field].get("alpha_beta_gy")
        if alpha_beta is not None:
            bed_id = next((key for key, meta in self.data.field_metadata.items() if "BED" in key and meta.get("alpha_beta_gy") is not None and np.isclose(float(meta["alpha_beta_gy"]), float(alpha_beta))), None)
            eqd2_id = next((key for key, meta in self.data.field_metadata.items() if "EQD2" in key and meta.get("alpha_beta_gy") is not None and np.isclose(float(meta["alpha_beta_gy"]), float(alpha_beta))), None)
            if bed_id: lines.append(f"s-BED: {float(self.data.fields[bed_id][point]):.6g} {self.data.field_metadata[bed_id]['units']}")
            if eqd2_id: lines.append(f"s-EQD2: {float(self.data.fields[eqd2_id][point]):.6g} {self.data.field_metadata[eqd2_id]['units']}")
            lines.append(f"Tissue parameter: α/β {float(alpha_beta):g} Gy")
        if survival is not None:
            value = float(survival[point]); lines.append(f"MLQ SF: {value:.6g}"); lines.append(f"−log₁₀(SF): {-np.log10(max(value, np.finfo(np.float32).tiny)):.6g}")
        if effect is not None: lines.append(f"MLQ K: {float(effect[point]):.6g}")
        lines.append(f"Region: {region}")
        contract = self.data.spatial_fields.get(field)
        if contract and contract.treatment_components:
            component_names = [str(item.get("component_id") or item.get("component_type") or "component") for item in contract.treatment_components]
            lines.append("Treatment components: " + ", ".join(component_names))
        self.voxel_chain.setText("\n".join(lines))

    def _viewer_tab_changed(self, index: int) -> None:
        if index == 1 and self.data is not None:
            self._mesh_timer.start(10)

    def _mesh_key(self) -> tuple[Any, ...]:
        smoothing = self._cad_smoothing()
        return (
            self._cad_mask_names(), self._cad_overlay_field(), self._scalar_range(),
            str(self.cad_mode.currentData() or "SURFACE"), self.cut_axis.currentText().lower(),
            self.cut_offset.value(), self.cut_invert.isChecked(), self.cut_azimuth.value(), self.cut_elevation.value(),
            self._resolved_isosurface_thresholds(), self.show_vertex_centres.isChecked(), self.show_neighbour_graph.isChecked(),
            self.cad_contours.isChecked(),
            (
                str(smoothing.get("method", "none")), int(smoothing.get("iterations", 0)),
                float(smoothing.get("lambda", 0.0)), float(smoothing.get("mu", 0.0)),
            ),
        )

    def _start_mesh_generation(self) -> None:
        if self.data is None:
            return
        anatomy_names = self._cad_mask_names(); overlay_field = self._cad_overlay_field()
        if not anatomy_names and not overlay_field:
            self.scene.clear(); self.mesh_status.setText("CAD display is off. Enable Anatomical CAD, s-BED overlay, or s-EQD2 overlay.")
            self.export_button.setEnabled(False); return
        key = self._mesh_key()
        if key in self._mesh_cache:
            self._apply_mesh_result(self._mesh_cache[key], cached=True); return
        settings = self._cad_smoothing(); scalar_range = self._scalar_range(); data = self.data
        mode = str(self.cad_mode.currentData() or "SURFACE"); cut_axis = self.cut_axis.currentText().lower()
        cut_fraction = self.cut_offset.value() / 100.0; cut_inverted = self.cut_invert.isChecked()
        cut_azimuth = float(self.cut_azimuth.value()); cut_elevation = float(self.cut_elevation.value())
        thresholds = self._resolved_isosurface_thresholds()
        show_contours = self.cad_contours.isChecked(); gtv_opacity = self.gtv_opacity.value() / 100.0
        oar_opacity = self.oar_opacity.value() / 100.0; iso_opacity = self.iso_opacity.value() / 100.0
        self._mesh_generation += 1; generation = self._mesh_generation
        self.mesh_status.setText("BUILDING — validated anatomical surfaces and biological overlay are generated outside the GUI thread."); self.export_button.setEnabled(False)
        worker = _MeshWorker(generation, lambda: _build_cad_scene_bundle(
            data, anatomy_names, overlay_field, settings, scalar_range,
            display_mode=mode, cut_axis=cut_axis, cut_fraction=cut_fraction,
            cut_inverted=cut_inverted, cut_azimuth_degrees=cut_azimuth, cut_elevation_degrees=cut_elevation,
            isosurface_thresholds=thresholds,
            show_contours=show_contours, gtv_opacity=gtv_opacity,
            oar_opacity=oar_opacity, isosurface_opacity=iso_opacity,
            show_vertex_centres=self.show_vertex_centres.isChecked(), show_graph=self.show_neighbour_graph.isChecked(),
        ))
        worker.cache_key = key
        self._mesh_workers.add(worker)
        worker.signals.finished.connect(self._mesh_finished); worker.signals.failed.connect(self._mesh_failed)
        self._thread_pool.start(worker)

    def _mesh_finished(self, generation: int, result: CADSceneBundle) -> None:
        self._mesh_workers = {item for item in self._mesh_workers if item.generation != generation}
        if generation != self._mesh_generation: return
        self._mesh_cache[self._mesh_key()] = result
        self._apply_mesh_result(result, cached=False)

    def _apply_mesh_result(self, result: CADSceneBundle, *, cached: bool) -> None:
        self.cad_bundle = result
        available = bool(result.anatomy_meshes or result.overlay_mesh or result.special_meshes)
        self.mesh_result = result.overlay_mesh or next(iter(result.special_meshes.values()), None) or next(iter(result.anatomy_meshes.values()), None)
        self.export_button.setEnabled(available)
        if not available:
            self.scene.clear(); self.mesh_status.setText("3D visualisation unavailable: no selected anatomical or biological surface passed display QC. Numerical results remain valid.")
            return
        self.scene.set_bundle(result, str(self.cad_region.currentData() or ""))
        vertex_count = sum(
            len(item.display_surface.vertices_lps_mm) for item in result.anatomy_meshes.values() if item.display_surface is not None
        )
        if result.overlay_mesh and result.overlay_mesh.display_surface is not None:
            vertex_count += len(result.overlay_mesh.display_surface.vertices_lps_mm)
        vertex_count += sum(len(item.display_surface.vertices_lps_mm) for item in result.special_meshes.values() if item.display_surface is not None)
        overlay = result.overlay_label or "OFF"
        failures = f" · {len(result.failures)} unavailable surface(s)" if result.failures else ""
        self.mesh_status.setText(
            f"{'CACHED' if cached else 'COMPLETED'} · {len(result.anatomy_meshes)} anatomical surface(s) · "
            f"mode {result.mode} · overlay {overlay} · {vertex_count} displayed vertices · DICOM patient LPS · "
            f"display smoothing {'ON' if result.smoothing_enabled else 'OFF'}{failures} · scientific voxel fields unchanged."
        )
        scalar_mesh = result.overlay_mesh or next(iter(result.special_meshes.values()), None)
        if scalar_mesh and scalar_mesh.display_surface is not None:
            values = np.asarray(scalar_mesh.display_surface.scalar_values, dtype=float)
            finite = values[np.isfinite(values)]
            value_range = f"{float(finite.min()):.4g}–{float(finite.max()):.4g} {result.overlay_units}" if finite.size else "no valid surface samples"
            qc = scalar_mesh.qc; coverage = float(qc.get("mesh_coverage_percent", qc.get("scalar_sampling_coverage_percent", 0.0)))
            alignment = str(qc.get("mesh_alignment_status") or ("GREEN" if coverage >= 99.0 else "AMBER"))
            median = qc.get("median_sampling_distance_mm"); maximum = qc.get("maximum_sampling_distance_mm")
            distance_text = f"median {float(median):.3g} mm · max {float(maximum):.3g} mm" if median is not None and maximum is not None else "sampling distance unavailable"
            self.cad_legend.setText(
                f"{result.mode.title()} · {result.overlay_label} · surface range {value_range} · ten fixed scalar bands. "
                f"FIELD VALIDATED | MESH {alignment} | COVERAGE {coverage:.2f}% | {distance_text} | PATIENT LPS | mm. "
                "Invalid samples are NaN, never zero. Smoothing affects display vertices only."
            )
            if result.overlay_field_id in self.data.field_metadata:
                meta = self.data.field_metadata[result.overlay_field_id]
                actual = tuple(meta["display_range"]); display = self._scalar_range() or actual
                self.colour_bar.set_scale(meta, display, actual)
        else:
            if result.overlay_field_id:
                reason = "; ".join(f"{key}: {value}" for key, value in result.failures.items()) or "BIOLOGY_FIELD_UNAVAILABLE"
                self.mesh_status.setText(f"BLOCKED — requested biological map was not rendered: {reason}. Neutral anatomy only; no fallback field was substituted.")
                self.cad_legend.setText("BIOLOGICAL MAP BLOCKED | INVALID VALUES REMAIN NaN | no fallback to physical dose, BED, EQD2, nearest-neighbour, or zero.")
            else:
                self.cad_legend.setText("Biological overlay OFF · showing smoothed validated anatomical masks only. GTV gold, vertices cyan, valleys violet, configured OARs magenta.")

    def _mesh_failed(self, generation: int, message: str) -> None:
        self._mesh_workers = {item for item in self._mesh_workers if item.generation != generation}
        if generation != self._mesh_generation: return
        self.scene.clear(); self.mesh_result = None; self.cad_bundle = None; self.export_button.setEnabled(False)
        self.mesh_status.setText(f"FAILED — 3D visualisation unavailable: {message}. Numerical results remain valid.")

    def _export(self) -> None:
        bundle = getattr(self, "cad_bundle", None)
        if bundle is None or not (bundle.anatomy_meshes or bundle.overlay_mesh or bundle.special_meshes): return
        folder = QFileDialog.getExistingDirectory(self, "Export Layer 3.1 biological surface")
        if not folder: return
        target = Path(folder); copied = []
        groups: list[tuple[str, BiologicalMeshResult]] = [
            (f"anatomy_{name}", mesh) for name, mesh in bundle.anatomy_meshes.items()
        ]
        if bundle.overlay_mesh is not None:
            groups.append((f"overlay_{bundle.overlay_field_id}", bundle.overlay_mesh))
        groups.extend((f"special_{label}", mesh) for label, mesh in bundle.special_meshes.items())
        for label, mesh in groups:
            safe = "".join(character if character.isalnum() else "_" for character in label).strip("_")
            for key in ("raw_stl", "stl", "vtp", "metadata"):
                source = mesh.artifacts.get(key)
                if source:
                    destination = target / f"layer31_{safe}_{key}{Path(source).suffix}"
                    shutil.copy2(source, destination); copied.append(destination.name)
        QMessageBox.information(self, "ASCEND Layer 3.1 export", f"Exported {len(copied)} files.")

    def _export_screenshot(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, "Export Spatial Biology Viewer", "layer31_spatial_biology.png", "PNG image (*.png)")
        if not path: return
        native_window = getattr(self.scene, "window", None)
        if callable(native_window):
            native_window = None
        screen = native_window.screen() if native_window is not None else None
        image = screen.grabWindow(int(native_window.winId())) if screen is not None else self.scene.grab()
        if image.isNull() or not image.save(path, "PNG"):
            QMessageBox.warning(self, "ASCEND Layer 3.1 export", "PNG_EXPORT_FAILED")
            return
        QMessageBox.information(self, "ASCEND Layer 3.1 export", "Exported the current quantitative 3D view as PNG.")

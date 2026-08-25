"""Adapt authoritative Layer 3.1 result artifacts for presentation.

The adapter verifies stored hashes, binds arrays to the validated Layer 1
geometry, and creates explicitly labelled display transforms.  Scientific
services remain the sole owners of BED, EQD2, MLQ, and TCP calculations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from ascend.gui.layer31_viewer_models import Layer31ViewerData
from ascend.layer3.spatial_biology import SpatialBiologyField, voxel_spacing_zyx_mm
from ascend.models.case import ASCENDCase
from ascend.scientific.legacy import layer21_validated as handoff
from ascend.validation.provenance import file_hash
from ascend.visualization.biology.handoff import biological_volume_from_stored_field
from ascend.visualization.biology.models import BiologicalVolume


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
            sf_floor = np.finfo(np.float32).tiny
            sf_clipped = np.clip(fields["voxel_survival_MLQ"], sf_floor, 1.0)
            sf_clipped_count = int(np.count_nonzero(sf_clipped != fields["voxel_survival_MLQ"]))
            fields["negative_log10_survival_MLQ"] = -np.log10(sf_clipped).astype(np.float32)
            metadata["negative_log10_survival_MLQ"] = {
                "label": "MLQ survival contrast · −log₁₀(SF)", "units": "log10 survival reduction", "alpha_beta_gy": None,
                "palette": "survival",
                "category": "3.1B tumour response · display transform", "equation": "−log₁₀[SF_MLQ(x)]",
                "interpretation": "Higher values mean lower model-predicted surviving fraction. Values are transformed for display only; numerical summaries retain SF.",
                "numerical_clipping_warning": "MLQ_SF_NUMERICAL_CLIPPING" if sf_clipped_count else None,
                "numerically_clipped_voxels": sf_clipped_count,
                "clipping_floor": float(sf_floor),
            }
            fields["course_effect_MLQ"] = -np.log(sf_clipped).astype(np.float32)
            metadata["course_effect_MLQ"] = {
                "label": "Accumulated MLQ effect", "units": "dimensionless effect", "alpha_beta_gy": None,
                "palette": "effect",
                "category": "3.1B tumour response", "equation": "K(x) = Σf [αdƒ+βG(xƒ)dƒ²]",
                "interpretation": "Accumulated model exponent before conversion to survival. Larger values produce lower modelled surviving fraction; this is not probability of control.",
                "numerical_clipping_warning": "MLQ_SF_NUMERICAL_CLIPPING" if sf_clipped_count else None,
                "numerically_clipped_voxels": sf_clipped_count,
                "clipping_floor": float(sf_floor),
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
    component_names = tuple(
        str(item.get("component_id") or item.get("component_type") or "component")
        for item in components
    )
    canonical_volumes: dict[str, BiologicalVolume] = {}
    for field_id in (
        "physical_course_dose_gy", "voxel_survival_MLQ", "course_effect_MLQ",
        *[key for key in fields if "BED" in key or "EQD2" in key],
    ):
        if field_id in canonical_volumes or field_id not in fields:
            continue
        canonical_volumes[field_id] = biological_volume_from_stored_field(
            fields[field_id], field_id, metadata[field_id], geometry, masks,
            treatment_components=component_names,
            provenance={"source_dose_uids": source_uids, "model_name": model_name},
        )
    return Layer31ViewerData(
        fields=fields, field_metadata=metadata, masks=masks, geometry=geometry,
        result=result, case_root=case.root, spatial_fields=contracts,
        vertex_centres_lps_mm=graph_nodes, graph_edges_lps_mm=tuple(graph_edges),
        biological_volumes=canonical_volumes,
    )

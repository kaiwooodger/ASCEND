"""Non-causal research-association presentation records for Layer 3.1."""

from __future__ import annotations

from typing import Any


def research_association_record(layer21: dict[str, Any] | None, layer31b: dict[str, Any], layer31c: dict[str, Any], layer22: dict[str, Any] | None) -> dict[str, Any]:
    """Expose independent variables and outcomes without asserting equations."""
    physical = {
        item.get("metric_id"): item.get("value")
        for item in (layer21 or {}).get("harmonised_metrics", [])
    }
    return {
        "schema_version": "ASCEND-L3.1-research-associations-v1",
        "relationship_type": "cohort_analysis_candidates_not_biological_equations",
        "physical_explanatory_variables": physical,
        "layer2_2_ipvdr": (layer22 or {}).get("plan_ipvdr", {}).get("primary_median"),
        "tumour_eud_gy": layer31b.get("tumour_eud_gy"),
        "modelled_therapeutic_ratio": layer31c.get("modelled_therapeutic_ratio"),
        "allowed_research_comparisons": ["HF_vs_EUD_T", "D_H_vs_EUD_T", "D_L_vs_EUD_T", "DR_vs_EUD_T", "DR_vs_TR", "iPVDR_vs_TR"],
        "causal_or_formula_claim": False,
    }

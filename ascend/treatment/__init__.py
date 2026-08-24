"""Treatment semantics kept outside numerical scientific kernels."""

from .applicability import ApplicabilityDecision, resolve_metric_applicability
from .models import TreatmentComponent, TreatmentContext

__all__ = [
    "ApplicabilityDecision", "TreatmentComponent", "TreatmentContext",
    "resolve_metric_applicability",
]

"""Independent validation evidence for regular anisotropic RTDOSE grids."""

from .comparison import ANISOTROPIC_GRIDS, run_anisotropic_validation
from .reporting import write_anisotropic_report

__all__ = ["ANISOTROPIC_GRIDS", "run_anisotropic_validation", "write_anisotropic_report"]

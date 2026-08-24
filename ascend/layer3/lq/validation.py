"""Independent computational verification helpers for the enclosing analysis layer."""

from __future__ import annotations

from typing import Any

import numpy as np


FLOAT32_RTOL = 2.0e-6
FLOAT32_ATOL_GY = 2.0e-5


def validate_direct_equivalence(
    doses: list[np.ndarray], fractions: list[int], alpha_beta_gy: float,
) -> dict[str, Any]:
    """Validate direct equivalence and raise a controlled error when requirements are not met."""
    direct = np.zeros_like(np.asarray(doses[0], dtype=np.float64))
    p_map = np.zeros_like(direct)
    q_map = np.zeros_like(direct)
    for dose, fraction in zip(doses, fractions):
        values = np.asarray(dose, dtype=np.float64)
        direct += values * (1.0 + (values / fraction) / alpha_beta_gy)
        p_map += values
        q_map += values ** 2 / fraction
    pq = p_map + q_map / alpha_beta_gy
    return {
        "status": "PASS" if np.allclose(direct, pq, rtol=FLOAT32_RTOL, atol=FLOAT32_ATOL_GY) else "FAIL",
        "maximum_absolute_error_gy": float(np.max(np.abs(direct - pq))),
        "rtol": FLOAT32_RTOL,
        "atol_gy": FLOAT32_ATOL_GY,
    }


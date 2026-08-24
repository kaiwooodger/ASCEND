"""Physical-coordinate analytic shapes used for anisotropic-grid verification."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class GridSpec:
    """A regular patient-coordinate grid; public spacing order is x, y, z."""

    name: str
    spacing_xyz_mm: tuple[float, float, float]
    extent_xyz_mm: tuple[float, float, float] = (48.0, 48.0, 48.0)

    @property
    def shape_zyx(self) -> tuple[int, int, int]:
        """Handle shape zyx for the enclosing ASCEND workflow."""
        dx, dy, dz = self.spacing_xyz_mm
        ex, ey, ez = self.extent_xyz_mm
        return (round(ez / dz), round(ey / dy), round(ex / dx))

    @property
    def spacing_zyx_mm(self) -> tuple[float, float, float]:
        """Handle spacing zyx mm for the enclosing ASCEND workflow."""
        dx, dy, dz = self.spacing_xyz_mm
        return dz, dy, dx

    @property
    def origin_xyz_mm(self) -> tuple[float, float, float]:
        """Handle origin xyz mm for the enclosing ASCEND workflow."""
        nz, ny, nx = self.shape_zyx
        dx, dy, dz = self.spacing_xyz_mm
        return (-0.5 * (nx - 1) * dx, -0.5 * (ny - 1) * dy, -0.5 * (nz - 1) * dz)

    @property
    def voxel_volume_cc(self) -> float:
        """Handle voxel volume cc for the enclosing ASCEND workflow."""
        return math.prod(self.spacing_xyz_mm) / 1000.0

    def physical_point(self, index_zyx: tuple[int, int, int]) -> np.ndarray:
        """Handle physical point for the enclosing ASCEND workflow."""
        z, y, x = index_zyx
        dx, dy, dz = self.spacing_xyz_mm
        ox, oy, oz = self.origin_xyz_mm
        return np.asarray([ox + x * dx, oy + y * dy, oz + z * dz], dtype=float)

    def coordinate_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Handle coordinate arrays for the enclosing ASCEND workflow."""
        nz, ny, nx = self.shape_zyx
        ox, oy, oz = self.origin_xyz_mm
        dx, dy, dz = self.spacing_xyz_mm
        z = oz + np.arange(nz, dtype=float) * dz
        y = oy + np.arange(ny, dtype=float) * dy
        x = ox + np.arange(nx, dtype=float) * dx
        return np.meshgrid(z, y, x, indexing="ij")[::-1]


def sphere_mask(grid: GridSpec, centre_xyz_mm: tuple[float, float, float], radius_mm: float) -> np.ndarray:
    """Handle sphere mask for the enclosing ASCEND workflow."""
    x, y, z = grid.coordinate_arrays()
    cx, cy, cz = centre_xyz_mm
    return (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2 <= radius_mm ** 2


def cuboid_mask(
    grid: GridSpec,
    centre_xyz_mm: tuple[float, float, float],
    lengths_xyz_mm: tuple[float, float, float],
) -> np.ndarray:
    """Handle cuboid mask for the enclosing ASCEND workflow."""
    x, y, z = grid.coordinate_arrays()
    return (
        (np.abs(x - centre_xyz_mm[0]) <= lengths_xyz_mm[0] / 2)
        & (np.abs(y - centre_xyz_mm[1]) <= lengths_xyz_mm[1] / 2)
        & (np.abs(z - centre_xyz_mm[2]) <= lengths_xyz_mm[2] / 2)
    )


def sphere_volume_cc(radius_mm: float) -> float:
    """Handle sphere volume cc for the enclosing ASCEND workflow."""
    return 4.0 * math.pi * radius_mm ** 3 / 3.0 / 1000.0


def cuboid_volume_cc(lengths_xyz_mm: tuple[float, float, float]) -> float:
    """Handle cuboid volume cc for the enclosing ASCEND workflow."""
    return math.prod(lengths_xyz_mm) / 1000.0


def sampled_volume_cc(mask: np.ndarray, grid: GridSpec) -> float:
    """Handle sampled volume cc for the enclosing ASCEND workflow."""
    return float(np.count_nonzero(mask) * grid.voxel_volume_cc)


def contour_stack_volume_cc(grid: GridSpec, area_at_z_mm2: Callable[[float], float]) -> float:
    """Independent trapezoidal contour-plane reconstruction used only by validation."""
    nz = grid.shape_zyx[0]
    oz = grid.origin_xyz_mm[2]
    dz = grid.spacing_xyz_mm[2]
    z = oz + np.arange(nz, dtype=float) * dz
    areas = np.asarray([max(0.0, float(area_at_z_mm2(value))) for value in z])
    selected = np.flatnonzero(areas > 0)
    if selected.size < 2:
        return float(areas.sum() * dz / 1000.0)
    first, last = int(selected[0]), int(selected[-1])
    return float(np.trapezoid(areas[first:last + 1], z[first:last + 1]) / 1000.0)


def relative_error_pct(measured: float, expected: float) -> float:
    """Handle relative error pct for the enclosing ASCEND workflow."""
    return 100.0 * (measured - expected) / expected

"""Install an ASCEND wheel into an isolated environment and smoke-test it."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import tempfile
import venv
from pathlib import Path


SMOKE_PROGRAM = r"""
import json
import os
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

import ascend
from ascend.gui.main_window import MainWindow
from ascend.layer3.lq.metrics import bed_values, eqd2_values_from_bed

dose = np.asarray([5.0, 20.0], dtype=np.float64)
bed = bed_values(dose, dose * dose, 10.0)
eqd2 = eqd2_values_from_bed(bed, 10.0)
assert np.allclose(bed, [7.5, 60.0], rtol=1e-12, atol=1e-12)
assert np.allclose(eqd2, [6.25, 50.0], rtol=1e-12, atol=1e-12)

application = QApplication.instance() or QApplication([])
window = MainWindow()
assert window.pages.count() >= 8
window.close()
application.processEvents()

package_path = Path(ascend.__file__).resolve()
checkout = Path(os.environ["GITHUB_WORKSPACE"]).resolve() if os.environ.get("GITHUB_WORKSPACE") else None
if checkout is not None:
    assert checkout not in package_path.parents, f"Imported ASCEND from checkout: {package_path}"

print(json.dumps({
    "ascend_version": ascend.__version__,
    "ascend_module": str(package_path),
    "bed_gy": bed.tolist(),
    "eqd2_gy": eqd2.tolist(),
    "gui_constructed": True,
}, sort_keys=True))
"""


def _environment_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def validate(wheel: Path, report_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="ascend-wheel-validation-") as directory:
        root = Path(directory)
        environment = root / "environment"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _environment_python(environment)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel.resolve())],
            check=True,
        )
        completed = subprocess.run(
            [str(python), "-c", SMOKE_PROGRAM],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        smoke = json.loads(completed.stdout.strip().splitlines()[-1])
        report = {
            "schema_version": "ASCEND-isolated-wheel-validation-v1",
            "runner_os": os.environ.get("RUNNER_OS", platform.system()),
            "runner_arch": os.environ.get("RUNNER_ARCH", platform.machine()),
            "python_version": platform.python_version(),
            "wheel": wheel.name,
            "smoke": smoke,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    wheels = sorted(args.dist.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected exactly one wheel in {args.dist}; found {len(wheels)}.")
    validate(wheels[0], args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

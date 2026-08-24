from __future__ import annotations

import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SourceIntegrityTests(unittest.TestCase):
    def test_validated_source_snapshots_are_byte_identical(self) -> None:
        expected = {
            "layer1_validated.py": "dfa1d6ba3e9ba4d49390b962e1cb04716a65a8d70320d37b729e86ec29c1c490",
            "layer21_validated.py": "4ddfa7eef71118db8edb40eba7331c3ee70a07021cd5386caf6f5f7c00cb3621",
            "layer22_validated.py": "2a45da69f21428078ec227fb69e0175168f0528d39432bdc60a3724b313eeb24",
        }
        for name, digest in expected.items():
            value = hashlib.sha256((ROOT / "ascend" / "scientific" / "legacy" / name).read_bytes()).hexdigest()
            self.assertEqual(value, digest, name)


if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ascend.layer1.cache import Layer1Cache, atomic_publish_directory, cache_key, cleanup_abandoned, verify_entry
from ascend.layer1.artifacts import deterministic_npz, streamed_scaled_float64_npy
from ascend.validation.provenance import file_hash


class Layer1CacheTests(unittest.TestCase):
    def test_case_local_cache_is_immutable_verified_and_independently_materialised(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            formal = root / "validated" / "run"
            formal.mkdir(parents=True)
            (formal / "layer1_result.json").write_text(json.dumps({"scientific": 1}), encoding="utf-8")
            (formal / "artifact.bin").write_bytes(b"unchanged-scientific-artifact")
            key = cache_key({"input_hashes": {"rtdose": "abc"}, "versions": {"algorithm": "v1"}})
            cache = Layer1Cache(root)
            entry = cache.publish(key, formal, {"algorithm": "v1"})
            self.assertTrue(verify_entry(entry)[0])
            destination = root / "validated" / "from-cache"
            cache.materialise(key, destination)
            self.assertEqual(file_hash(formal / "artifact.bin"), file_hash(destination / "artifact.bin"))
            (destination / "artifact.bin").write_bytes(b"formal-run-change")
            self.assertTrue(verify_entry(entry)[0])
            self.assertNotEqual(file_hash(entry / "artifact.bin"), file_hash(destination / "artifact.bin"))

    def test_cache_key_changes_with_algorithm_or_input(self) -> None:
        first = cache_key({"input": "a", "algorithm": "v1"})
        self.assertNotEqual(first, cache_key({"input": "b", "algorithm": "v1"}))
        self.assertNotEqual(first, cache_key({"input": "a", "algorithm": "v2"}))

    def test_deterministic_npz_is_bitwise_identical(self) -> None:
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            arrays = {"dose_gy": np.arange(24, dtype=np.float32).reshape(2, 3, 4), "GTV": np.ones((2, 3, 4), dtype=np.uint8)}
            deterministic_npz(root / "first.npz", arrays)
            deterministic_npz(root / "second.npz", dict(reversed(list(arrays.items()))))
            self.assertEqual(file_hash(root / "first.npz"), file_hash(root / "second.npz"))

    def test_streamed_native_dose_is_bitwise_identical_to_numpy_save(self) -> None:
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pixels = np.arange(60, dtype=np.uint16).reshape(3, 4, 5)
            np.save(root / "reference.npy", pixels.astype(float) * 0.017, allow_pickle=False)
            streamed_scaled_float64_npy(root / "streamed.npy", pixels, 0.017)
            self.assertEqual(file_hash(root / "reference.npy"), file_hash(root / "streamed.npy"))

    def test_clear_requires_confirmation_and_does_not_remove_validated_results(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            formal = root / "validated" / "run"
            formal.mkdir(parents=True)
            (formal / "layer1_result.json").write_text("{}", encoding="utf-8")
            cache = Layer1Cache(root)
            cache.publish(cache_key({"x": 1}), formal, {"algorithm": "v1"})
            with self.assertRaisesRegex(ValueError, "confirmation"):
                cache.clear()
            self.assertEqual(cache.clear(confirmed=True), 1)
            self.assertTrue((formal / "layer1_result.json").is_file())

    def test_formal_directory_is_only_visible_after_atomic_publication(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / ".tmp-run-interrupted"
            destination = root / "layer1_final"
            staging.mkdir(); (staging / "layer1_result.json").write_text("{}", encoding="utf-8")
            self.assertFalse(destination.exists())
            atomic_publish_directory(staging, destination)
            self.assertTrue((destination / "layer1_result.json").is_file())
            abandoned = root / ".tmp-abandoned"; abandoned.mkdir(); (abandoned / "partial").write_text("x")
            cleanup_abandoned(root)
            self.assertFalse(abandoned.exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ascend.gui.layer22_viewer import DoseSliceCanvas, export_viewer_meshes, full_graph_surface, prepare_layer22_viewer_data
from ascend.layer2.graph.service import Layer22Service
from ascend.models.case import ASCENDCase

from .helpers import synthetic_case


class Layer22ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_connected_component_masks_generate_patient_coordinate_meshes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer2_2 = Layer22Service().run(case)
            data = prepare_layer22_viewer_data(case)
            self.assertEqual(data.vertex_source, "connected_components_derived")
            self.assertEqual(len(data.vertex_meshes), 4)
            self.assertEqual(set(data.vertex_meshes), {item["node"] for item in data.nodes})
            self.assertEqual(data.gtv_mesh.vertices_lps_mm.shape[1], 3)
            self.assertGreater(len(data.gtv_mesh.faces), 0)
            self.assertEqual(data.vertex_union.shape, data.dose_gy.shape)

    def test_explicit_vertices_dose_overlay_and_stl_export(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            case = synthetic_case(root / "case", explicit_vertices=True)
            case.layer2_2 = Layer22Service().run(case)
            data = prepare_layer22_viewer_data(case)
            self.assertEqual(data.vertex_source, "explicit_rtstruct_vertices")
            canvas = DoseSliceCanvas("axial")
            canvas.set_data(data)
            canvas.set_midpoint(data.edges[0]["midpoint_lps_mm"])
            image = canvas._image()
            self.assertIsNotNone(image)
            self.assertEqual(image.width(), data.dose_gy.shape[2])
            self.assertEqual(image.height(), data.dose_gy.shape[1])
            outputs = export_viewer_meshes(data, root / "stl")
            self.assertEqual(len(outputs), 7)
            self.assertTrue(all(path.is_file() and path.stat().st_size > 84 for path in outputs[:-1]))
            manifest = json.loads(outputs[-1].read_text(encoding="utf-8"))
            self.assertEqual(manifest["coordinate_system"], "DICOM patient LPS")
            self.assertEqual(len(manifest["connections"]), len(data.edges))
            self.assertEqual(manifest["full_vertex_graph"], "Full_vertex_graph_connections_LPS_mm.stl")
            self.assertEqual(manifest["connection_tube_radius_mm"], 0.85)
            graph = full_graph_surface(data)
            vertex_face_count = sum(len(mesh.faces) for mesh in data.vertex_meshes.values())
            self.assertGreater(len(graph.faces), vertex_face_count)
            self.assertTrue(np.isfinite(graph.vertices_lps_mm).all())

    def test_midpoint_sphere_overlay_uses_physical_spacing_and_slice_intersection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            case = synthetic_case(Path(folder))
            case.layer2_2 = Layer22Service().run(case)
            data = prepare_layer22_viewer_data(case)
            canvas = DoseSliceCanvas("axial")
            canvas.set_data(data)
            canvas.set_midpoint(data.edges[0]["midpoint_lps_mm"])
            overlay = canvas._midpoint_overlay_geometry(data.dose_gy.shape[1])
            self.assertIsNotNone(overlay)
            _horizontal, _vertical, radius_x, radius_y = overlay
            self.assertAlmostEqual(radius_x, 3.0 / float(data.geometry["spacing"][1]), places=5)
            self.assertAlmostEqual(radius_y, 3.0 / float(data.geometry["spacing"][0]), places=5)
            offsets = np.asarray(data.geometry["offsets"], dtype=float)
            if len(offsets) > 1:
                farthest = int(np.argmax(np.abs(offsets - offsets[canvas.index])))
                canvas.set_index(farthest)
                self.assertIsNone(canvas._midpoint_overlay_geometry(data.dose_gy.shape[1]))

    def test_moved_case_relocates_internal_project_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "old" / "case"
            moved = root / "new" / "case"
            synthetic_case(original)
            payload = json.loads((original / "ascend_case.json").read_text(encoding="utf-8"))
            moved.mkdir(parents=True)
            payload["case_root"] = "/missing/original/case"
            payload["layer1"]["result_path"] = payload["layer1"]["result_path"].replace(str(original), "/missing/original/case")
            manifest = payload["layer1"]["result"]["manifest"]
            manifest["validated_native_dose"]["path"] = manifest["validated_native_dose"]["path"].replace(str(original), "/missing/original/case")
            manifest["mask_export"]["path"] = manifest["mask_export"]["path"].replace(str(original), "/missing/original/case")
            (moved / "ascend_case.json").write_text(json.dumps(payload), encoding="utf-8")
            loaded = ASCENDCase.load(moved / "ascend_case.json")
            self.assertEqual(loaded.root, moved.resolve())
            self.assertTrue(str(loaded.layer1.result_path).startswith(str(moved.resolve())))


if __name__ == "__main__":
    unittest.main()

"""Contextual, display-only instructions for ASCEND interactive viewers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)


GuideSection = tuple[str, tuple[tuple[str, str], ...]]


VIEWER_GUIDES: dict[str, tuple[str, tuple[GuideSection, ...]]] = {
    "layer2_1": (
        "The Layer 2.1 viewer presents stored per-vertex physical QA. Navigation changes only the display; it never recalculates dose, FWHM, coverage, volume, or distance.",
        (
            ("Layout and navigation", (
                ("Projection", "Automatic chooses the two patient-coordinate axes with the greatest vertex spread. Axial, sagittal, and coronal force X–Y, Y–Z, and X–Z views."),
                ("Mouse wheel / + / −", "Zooms the vertex layout. It does not change physical coordinates or distances."),
                ("Left-drag", "Pans the layout within the viewport."),
                ("Rotate left / right", "Rotates the 2D presentation by 15 degrees. Patient-coordinate values remain unchanged."),
                ("Fit", "Restores the default zoom, rotation, and pan."),
                ("Vertex labels", "Shows or hides the vertex identifier and local FWHM beside each marker."),
                ("Distance labels", "Shows or hides the stored centroid-to-centroid nearest-neighbour distance on each connection."),
                ("Hover over a vertex", "Opens the QA card containing D95, V95 RxH, mean and maximum dose, volume, nearest vertex and distance, local FWHM, native-axis FWHM, and warnings."),
            )),
            ("Visual encoding", (
                ("Marker colour", "Encodes local FWHM using the fixed low-to-high blue–purple legend. Grey means no valid stored FWHM."),
                ("Marker size", "Encodes stored vertex volume. Larger markers represent larger geometric volumes."),
                ("Connection line", "Represents a stored nearest-neighbour relationship, not beam direction or a dose path."),
                ("Local FWHM", "Full width at half maximum measured for the individual vertex dose profile. It is reported in millimetres."),
                ("Native X / Y / Z FWHM", "Axis-specific half-maximum widths on the native dose grid; differences indicate directional variation."),
            )),
            ("Global FWHM tab", (
                ("Average global FWHM", "Arithmetic mean of valid local vertex FWHM values; more sensitive to unusually large or small vertices."),
                ("Median global FWHM", "Middle valid local FWHM value; the primary robust centre of the distribution."),
                ("Observed range", "Minimum through maximum valid local FWHM."),
                ("Per-vertex table", "Lists local and native-axis FWHM and the half-maximum dose used for each vertex."),
            )),
        ),
    ),
    "layer2_2": (
        "The Layer 2.2 viewer presents the locked nearest-neighbour midpoint-iPVDR graph plus additive vertex-profile and dose-saddle evidence. Display controls never alter stored physics.",
        (
            ("Graph overview", (
                ("Projection", "Chooses automatic, axial, sagittal, or coronal patient-coordinate projection."),
                ("Edge labels", "Shows the active edge metric beside each connection. A dash means no valid stored value."),
                ("Invalid edges", "Includes or hides explicitly excluded edges. Hidden edges remain in provenance and exports."),
                ("Zoom / rotate / Fit / drag", "Changes only the graph presentation. Fit resets zoom, rotation, and pan."),
                ("Node", "A validated high-dose vertex centroid. Node D50 is the median dose inside its vertex mask."),
                ("Connection", "An unchanged nearest-neighbour Layer 2.2 edge between two vertex centroids."),
                ("Midpoint marker", "The geometric midpoint and centre of the locked 3 mm valley-sphere measurement."),
            )),
            ("Vertex profiles", (
                ("Vertex selector / table row", "Selects one stored vertex profile and highlights the same vertex in the 3D CAD view."),
                ("Overlay all vertices", "Draws every stored radial profile together for comparison."),
                ("Background-corrected profile", "Switches from absolute Gy to excess-dose profile P(r), where 1 is the vertex core and 0 is local background."),
                ("Show shell mean", "Adds the mean dose curve. The median curve remains the primary robust profile."),
                ("IQR band", "Shows the 25th-to-75th percentile dose within each physical spherical shell."),
                ("r80 / r50 / r20", "First outward crossings of 80%, 50%, and 20% of background-corrected modulation amplitude."),
                ("Geometric-radius marker", "Half of the equivalent-sphere diameter calculated from vertex volume."),
                ("Dose diameter", "Twice r50. It is compared with the geometric equivalent diameter."),
                ("Penumbra", "r20 minus r80, in millimetres."),
                ("Maximum gradient", "Largest outward dose-falloff magnitude from the unsmoothed median shell profile, in Gy/mm."),
            )),
            ("Saddle graph", (
                ("Midpoint PVDR", "Colours edges by the unchanged endpoint-D50 to geometric-midpoint D50 ratio."),
                ("Saddle PVDR", "Colours edges by endpoint-D50 divided by robust local saddle D50."),
                ("Midpoint − saddle dose", "Colours edges by geometric-midpoint D50 minus local saddle D50, in Gy."),
                ("Edge length", "Colours edges by physical centroid-to-centroid distance in millimetres."),
                ("Validation status", "Uses green for valid saddle edges and red for explicitly excluded edges."),
                ("Saddle marker", "Distinct orange square or sphere at the deterministic bottleneck location."),
                ("Saddle path", "Shows the optional 26-connected widest path that maximises its minimum native-grid dose."),
                ("Diagnostic corridor", "Shows the capsule search region. It is clipped to GTV and excludes unrelated vertices."),
                ("Select edge", "Synchronises graph/CAD selection and shows endpoint D50, midpoint dose, saddle dose, both PVDR values, displacement, radius, status, and warnings."),
            )),
            ("3D and orthogonal views", (
                ("GTV / vertex masks / connections", "Shows or hides the anatomical envelope, vertex surfaces, and graph cylinders."),
                ("Dose heatmap", "Shows or hides the native-dose overlay in the orthogonal slice views."),
                ("Perspective / axial / sagittal / coronal", "Sets the 3D camera orientation."),
                ("Zoom / rotate", "Moves the 3D camera without modifying stored geometry."),
                ("Selected connection", "Focuses one edge across the evidence table, orthogonal views, and 3D graph."),
                ("Export STL meshes", "Writes display geometry and a manifest. It does not export or recalculate numerical metrics."),
            )),
        ),
    ),
    "layer3_1": (
        "The Layer 3.1 unified viewer displays stored physical dose and research radiobiological fields on one linked anatomy. Read maps first, whole-tumour results second, and regional explanation third.",
        (
            ("Field and anatomy", (
                ("Displayed biological quantity / map tabs", "Selects physical dose, spatial BED, spatial EQD2, MLQ −log10(SF), surviving fraction, or MLQ effect K from stored fields."),
                ("Tissue / anatomical focus", "Chooses the ROI used for map context, statistics, and 3D focus."),
                ("Structures", "Shows or hides anatomical boundaries across linked views."),
                ("High-dose LQ warning", "Shows or hides voxels beyond the configured LQ extrapolation warning criterion. It does not change the model."),
                ("GTV / Vertices / Valleys / OARs", "Independently controls anatomical groups."),
                ("C1 / C2 / C3", "Requests a stored-service recalculation for a standard tumour-sensitivity scenario. This is the only viewer control that requests new science."),
            )),
            ("Colour range", (
                ("Robust 2–98 percentile", "Uses the complete field's 2nd and 98th percentiles to reduce domination by extreme voxels."),
                ("Full range", "Uses the complete stored minimum and maximum."),
                ("Manual fixed range", "Uses entered minimum and maximum values."),
                ("Percentile", "Uses entered lower and upper complete-field percentiles."),
                ("Apply range", "Applies the selected display range to the maps."),
                ("Lock scale", "Keeps one range across linked views and comparisons so colours retain the same meaning."),
                ("Display-only anatomical smoothing", "Smooths presentation surfaces only. Stored voxel fields and exported numerical results remain unsmoothed."),
            )),
            ("Linked four-pane navigation", (
                ("Axial / sagittal / coronal sliders", "Moves the shared crosshair through each patient-coordinate plane."),
                ("Click a 2D plane or pick 3D", "Moves all views to the same physical DICOM LPS point and updates the local value readout."),
                ("Mouse wheel", "Zooms linked views. Drag pans; CAD left-drag rotates and middle-drag pans."),
                ("Perspective / axial / sagittal / coronal", "Sets the CAD camera and focuses the corresponding slice plane."),
                ("Zoom in / out, rotate, Fit all", "Applies one navigation operation to the linked 2D and 3D workspace. Fit restores the complete anatomy."),
                ("Metric strip", "Mean, maximum, D95, and minimum are stored summary values for the active field and ROI, not values derived by the renderer."),
            )),
            ("CAD controls", (
                ("3D mode", "Selects surface map, true volume, isosurfaces, orthogonal slices, or combined biology rendering."),
                ("Region focus", "Limits the display focus to whole GTV, vertices, valleys, or other GTV."),
                ("Tissue parameter", "Chooses the stored tissue-specific BED/EQD2 field when multiple assignments exist."),
                ("Vertex centres / Layer 2.2 graph / contour bands", "Adds location markers, the physical neighbour graph, or biological contour bands."),
                ("Cut plane / position / invert / azimuth / elevation", "Controls the diagnostic 3D clipping plane."),
                ("Isosurface thresholds", "Accepts percentiles such as P90 or field values with units for display surfaces."),
                ("Biological Landscape", "Applies a display preset only."),
                ("GTV / OAR / biology opacity and volume preset", "Adjusts transparency and transfer-function presentation."),
                ("Export STL + VTP / PNG", "Exports current display geometry or a screenshot. Scientific exports remain separate."),
            )),
            ("Results and interpretation", (
                ("Comparison", "Displays stored maps side by side under a common interpretation and colour scale."),
                ("Whole-tumour SF / EUD", "Reports configured MLQ whole-tumour survival and effect-equivalent dose summaries."),
                ("Regional contribution", "Decomposes residual-survivor contribution into vertex, valley, and other-GTV regions; segments sum to 100%."),
                ("Research boundary", "BED, EQD2, MLQ survival, effect K, EUD, and therapeutic-ratio outputs are comparative model results, not TCP, NTCP, toxicity, or treatment recommendations."),
            )),
        ),
    ),
    "layer3_2": (
        "The Layer 3.2 viewer presents stored local and non-local research-model fields, anatomical boundaries, biological consequence surfaces, and unchanged Layer 2.2 graph sampling evidence.",
        (
            ("Common 2D controls", (
                ("Stored field", "Selects the physical, baseline-survival, mediator-exposure, scaled-exposure, final-survival, reduction, or effect-equivalent stored field."),
                ("2D plane", "Selects axial, sagittal, or coronal patient-coordinate orientation."),
                ("Scale", "Absolute uses the field definition's complete scale; case-relative expands contrast for exploratory within-case viewing."),
                ("Comparison", "Chooses the synchronized comparison arrangement. Unavailable scenarios are labelled rather than synthesised."),
                ("Zoom / rotate / Fit", "Applies the same presentation transform to synchronized 2D panels."),
                ("Synchronized slice", "Moves every 2D comparison panel to the same stored crop index."),
                ("Click a voxel", "Selects the same location across panels and displays its stored calculation chain."),
            )),
            ("Comparison workspace", (
                ("Three-panel consequence", "Shows baseline LQ survival, final survival, and additional modelled reduction together."),
                ("Baseline versus final", "Directly compares local-only and final modelled survival."),
                ("No-sink versus anatomical-sink", "Shows the configured sensitivity comparison only when a compatible stored scenario exists."),
                ("Physical versus effect-equivalent dose", "Compares physical Gy with the configured model's effect-equivalent dose field."),
                ("Difference map", "Shows the stored absolute difference for the selected compatible comparison."),
                ("Field interpretation / calculation chain", "Defines the active field and lists stored voxel-level inputs and outputs. It does not infer clinical risk."),
            )),
            ("3D biological volume", (
                ("3D mode", "Selects full volume, isosurfaces, orthogonal slices, or combined rendering."),
                ("Region focus", "Restricts the rendered model domain to the complete crop, GTV, vertices, or an available OAR."),
                ("Opacity preset", "Chooses biological-effect, high-effect, or linear transfer-function emphasis."),
                ("Relative isovalue", "Controls a field-relative display isosurface when absolute consequence surfaces are disabled."),
                ("Absolute consequence surfaces", "Uses stored additional-survival-reduction thresholds of 2.5%, 5%, 10%, and 20%."),
                ("Opacity", "Changes rendered field transparency."),
                ("Clipping orientation / position", "Cuts the 3D volume along an axial, coronal, or sagittal plane."),
                ("GTV / vertices / OARs / crop boundary", "Shows or hides anatomical surfaces and the model-domain wireframe."),
                ("Perspective / axial / sagittal / coronal", "Sets the 3D camera orientation. Zoom and rotate move the camera only."),
                ("Export VTI / VTP / PLY / GLB / 3MF / STL", "Exports stored fields and display geometry with their supported formats."),
                ("Export view PNG", "Captures the current 3D presentation."),
            )),
            ("Probe and graph profile", (
                ("Voxel probe Z / Y / X", "Selects a native stored-crop voxel and reports values from every available field."),
                ("Model-crop boundary", "The wireframe is the GTV-plus-margin computation crop, not a whole-patient model domain."),
                ("Single field", "Shows the selected scalar field in one large 2D panel."),
                ("Edge profile", "Selects one unchanged Layer 2.2 edge and plots stored physical and biological samples along it."),
                ("Graph-profile boundary", "The profile is visual evidence only. Layer 3.2 iPVDR retains the locked Layer 2.2 node and 3 mm midpoint-sphere definitions."),
                ("Research boundary", "Mediator fields and survival changes are modelled quantities, not measured concentrations, toxicity probabilities, clinical risks, or treatment recommendations."),
            )),
        ),
    ),
}


class ViewerGuideDialog(QDialog):
    """Non-modal reference manual that preserves the active viewer state."""

    def __init__(self, layer_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        intro, sections = VIEWER_GUIDES[layer_key]
        layer_title = layer_key.replace("layer", "Layer ").replace("_", ".")
        self.setWindowTitle(f"ASCEND — {layer_title} interactive viewer guide")
        self.setModal(False)
        self.resize(840, 600)
        layout = QVBoxLayout(self)
        introduction = QLabel(intro)
        introduction.setObjectName("sectionDescription")
        introduction.setWordWrap(True)
        introduction.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(introduction)
        self.tabs = QTabWidget()
        for title, rows in sections:
            table = QTableWidget(len(rows), 2)
            table.setHorizontalHeaderLabels(["Control / item", "Function and interpretation"])
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            table.setWordWrap(True)
            for row, (control, function) in enumerate(rows):
                table.setItem(row, 0, QTableWidgetItem(control))
                table.setItem(row, 1, QTableWidgetItem(function))
            table.resizeRowsToContents()
            self.tabs.addTab(table, title)
        layout.addWidget(self.tabs, 1)
        note = QLabel("Viewer controls are presentation-only unless the guide explicitly states that a scientific-service recalculation is requested.")
        note.setObjectName("sectionDescription")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.hide)
        layout.addWidget(buttons)


def show_viewer_guide(parent: QWidget, layer_key: str) -> ViewerGuideDialog:
    """Show one persistent guide instance per parent and layer."""
    guides: dict[str, ViewerGuideDialog] = getattr(parent, "_viewer_guide_dialogs", {})
    dialog = guides.get(layer_key)
    if dialog is None:
        dialog = ViewerGuideDialog(layer_key, parent)
        guides[layer_key] = dialog
        setattr(parent, "_viewer_guide_dialogs", guides)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def guide_rows(layer_key: str) -> list[tuple[str, str]]:
    """Return flattened guide rows for accessibility and regression tests."""
    return [row for _title, rows in VIEWER_GUIDES[layer_key][1] for row in rows]

"""Cohesive Qt page construction for the ASCEND workstation."""


class WorkstationPageBuilderMixin:
    """Build workstation pages without owning controller or workflow state."""

    def _build_pages(self) -> None:
        self._build_import_page()
        self._build_configuration_page()
        self._build_mapping_page()
        self._build_layer1_page()
        self._build_layer21_page()
        self._build_layer22_page()
        self._build_layer31_page()
        self._build_layer32_page()
        self._build_review_page()
        self._build_export_page()

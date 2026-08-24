"""Explicit non-implemented interfaces reserved for future biological modelling layers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Layer31Interface(ABC):
    """Future BED/EQD2 service contract. No calculation is implemented."""

    @abstractmethod
    def run(self, validated_case: Any, biological_configuration: dict[str, Any]) -> Any:
        """Execute run and return its explicit calculation state and evidence."""
        raise NotImplementedError("Layer 3.1 is not implemented.")


class Layer32PluginInterface(ABC):
    """Future biological-model plug-in contract. No model is implemented."""

    @abstractmethod
    def prepare(self, validated_case: Any, inputs: dict[str, Any]) -> Any:
        """Prepare validated inputs for a future biological-model plug-in."""
        raise NotImplementedError

    @abstractmethod
    def run(self) -> Any:
        """Execute a future plug-in calculation."""
        raise NotImplementedError

    @abstractmethod
    def summarise(self) -> Any:
        """Return a structured summary of future plug-in output."""
        raise NotImplementedError

    @abstractmethod
    def validate(self) -> Any:
        """Validate a future plug-in configuration and its prerequisites."""
        raise NotImplementedError

#!/usr/bin/env python3
"""Validate Copilot tooling runtime prerequisites before automated review."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


CONFIG_PATH = Path(".github/copilot-runtime.json")


def fail(message: str) -> None:
    print(f"TOOLING_PREFLIGHT_FAILED: {message}")
    raise SystemExit(1)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        fail(f"missing runtime config at {CONFIG_PATH}")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {CONFIG_PATH}: {exc}")


def parse_available_models() -> list[str]:
    raw = os.environ.get("COPILOT_AVAILABLE_MODELS", "")
    models = [item.strip() for item in raw.split(",") if item.strip()]
    if not models:
        fail(
            "COPILOT_AVAILABLE_MODELS is empty; export a comma-separated list of "
            "supported models before running review tooling."
        )
    return models


def main() -> int:
    config = load_config()
    review = config.get("code_review")
    if not isinstance(review, dict):
        fail("code_review config block is missing")

    primary = review.get("primary_model")
    fallback = review.get("fallback_models")
    if not isinstance(primary, str) or not primary:
        fail("code_review.primary_model must be a non-empty string")
    if not isinstance(fallback, list) or not all(
        isinstance(item, str) and item for item in fallback
    ):
        fail("code_review.fallback_models must be a non-empty list of strings")

    available_models = parse_available_models()
    ordered_candidates = [primary, *fallback]
    selected_model = next(
        (candidate for candidate in ordered_candidates if candidate in available_models),
        None,
    )
    if selected_model is None:
        fail(
            "no configured code_review model is supported in this runtime. "
            f"Configured: {ordered_candidates}. Available: {available_models}."
        )

    print(
        "TOOLING_PREFLIGHT_OK: selected_code_review_model="
        f"{selected_model}; available_models={available_models}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

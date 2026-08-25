"""Tests for Database Scaling & Full Nutritional Universe Ingestion."""

from __future__ import annotations

import pytest

from pathyam_engine.authoring.full_ifct_dataset import (
    FULL_IFCT_FOOD_CATALOG,
    FULL_NUTRIENT_CATALOG,
    get_full_universe_composition,
)
from pathyam_engine.repository import InMemoryRepository


def test_full_nutritional_universe_catalog_scale():
    assert len(FULL_IFCT_FOOD_CATALOG) == 528, f"Expected 528 IFCT foods, got {len(FULL_IFCT_FOOD_CATALOG)}"
    assert len(FULL_NUTRIENT_CATALOG) == 42, f"Expected 42 nutrients, got {len(FULL_NUTRIENT_CATALOG)}"

    composition, food_meta = get_full_universe_composition()
    assert len(food_meta) == 528
    assert len(composition) == 528

    total_analytical_points = sum(len(v) for v in composition.values())
    assert total_analytical_points > 4000, f"Expected >4000 analytical points, got {total_analytical_points}"


def test_full_universe_repository_query_performance():
    composition, food_meta = get_full_universe_composition()

    repo = InMemoryRepository(
        nutrients=FULL_NUTRIENT_CATALOG,
        composition=composition,
        food_meta=food_meta,
    )

    # Test single and batch lookups across 528 foods
    nutrients = repo.get_nutrients()
    assert len(nutrients) == 42

    comp_batch = repo.get_composition(list(range(1, 100)))
    assert len(comp_batch) == 99
    assert all(len(vals) >= 8 for vals in comp_batch.values())

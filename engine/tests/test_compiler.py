"""Tests for Recipe Compiler and Phase 0 Data Foundation."""

from __future__ import annotations

from pathlib import Path
import pytest

from pathyam_engine.authoring import load_library
from pathyam_engine.authoring.ifct_data import build_in_memory_repository
from pathyam_engine.compiler import RecipeCompiler, RecipeState
from pathyam_engine.repository import InMemoryRepository

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "db" / "templates"


@pytest.fixture
def library():
    return load_library(
        TEMPLATE_DIR / "ingredients.yaml",
        sorted(p for p in TEMPLATE_DIR.glob("*.yaml") if p.name != "ingredients.yaml"),
    )


@pytest.fixture
def full_ifct_repo(library) -> InMemoryRepository:
    return build_in_memory_repository(library)


def test_recipe_compiler_all_17_templates_are_computable(library, full_ifct_repo):
    compiler = RecipeCompiler(full_ifct_repo)
    report = compiler.compile_library(library)

    assert report.total >= 17, f"Expected at least 17 templates, found {report.total}"
    assert report.coverage == 1.0, (
        f"Expected 100% coverage, got {report.coverage * 100:.1f}%. "
        f"Blocked templates: {[tid for tid, u in report.units.items() if not u.is_computable]}"
    )

    for tid, unit in report.units.items():
        assert unit.state == RecipeState.COMPUTABLE, (
            f"Template {tid} ({unit.dish_name}) state is {unit.state}, reasons: {unit.reasons}"
        )
        assert unit.energy_per_100g is not None and unit.energy_per_100g > 0.0


def test_recipe_compiler_catches_unresolved_ingredient(library, full_ifct_repo):
    compiler = RecipeCompiler(full_ifct_repo)

    # Empty repository with no composition data
    empty_repo = InMemoryRepository()
    empty_compiler = RecipeCompiler(empty_repo)

    tpl = library.templates["PY-T-000110"]
    bad_unit = empty_compiler.compile_template(
        "PY-T-000110",
        authored_template=tpl,
        library=library,
    )
    assert bad_unit.state in (RecipeState.RESOLUTION_REQUIRED, RecipeState.MISSING_COMPOSITION)

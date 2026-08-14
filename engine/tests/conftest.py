"""Shared fixtures: a miniature dosa dataset held entirely in memory.

Mirrors the structure of ``db/010_seed_example.sql`` closely enough to exercise the
engine, including a nested sub-recipe, without needing a database.
"""

from __future__ import annotations

import pytest

from pathyam_engine.models import (
    CompositionValue, Nutrient, RecipeTemplate, RetentionFactor, SourceRef,
    TemplateIngredient, TemplateParameter,
)
from pathyam_engine.repository import InMemoryRepository

RICE, URAD, GINGELLY, GHEE, POTATO, ONION, DOSA, MASALA_DOSA, FILLING = 1, 2, 3, 4, 5, 6, 100, 101, 102

NUTRIENTS = [
    Nutrient(1, "ENERC_KCAL", "Energy", "kcal", 0, "proximate", True),
    Nutrient(2, "PROCNT", "Protein", "g", 1, "proximate", True),
    Nutrient(3, "FAT", "Total fat", "g", 1, "lipid", True),
    Nutrient(4, "CHOAVLDF", "Available carbohydrate", "g", 1, "carbohydrate", True),
    Nutrient(5, "K", "Potassium", "mg", 0, "mineral", True),
    Nutrient(6, "THIA", "Thiamine", "mg", 3, "vitamin", False),
]

# (nutrient_id, value, sd) per 100 g
_COMP = {
    RICE:     [(1, 346.0, 6.2), (2, 7.81, 0.42), (3, 0.52, 0.09),
               (4, 74.80, 1.10), (5, 115.0, 12.0), (6, 0.28, 0.03)],
    URAD:     [(1, 341.0, 7.1), (2, 23.02, 0.90), (3, 1.64, 0.18),
               (4, 52.10, 1.40), (5, 983.0, 45.0), (6, 0.25, 0.03)],
    GINGELLY: [(1, 900.0, 0.0), (3, 100.0, 0.0)],
    GHEE:     [(1, 900.0, 0.0), (3, 100.0, 0.0)],
    POTATO:   [(1, 87.0, 3.0), (2, 1.87, 0.20), (3, 0.10, 0.03),
               (4, 18.40, 0.90), (5, 380.0, 30.0)],
    ONION:    [(1, 46.0, 2.5), (2, 1.20, 0.15), (3, 0.10, 0.02),
               (4, 9.34, 0.60), (5, 146.0, 14.0)],
}

FOOD_META = {
    RICE: {"name": "Rice, parboiled, milled", "food_group": "cereal"},
    URAD: {"name": "Black gram dhal", "food_group": "pulse"},
    GINGELLY: {"name": "Gingelly oil", "food_group": "fat"},
    GHEE: {"name": "Ghee, cow", "food_group": "fat"},
    POTATO: {"name": "Potato, boiled", "food_group": "vegetable"},
    ONION: {"name": "Onion, big", "food_group": "vegetable"},
    DOSA: {"name": "Dosa, plain", "food_group": "prepared_dish"},
    MASALA_DOSA: {"name": "Dosa, masala", "food_group": "prepared_dish"},
    FILLING: {"name": "Potato masala filling", "food_group": "prepared_dish"},
}

_BATTER_PARAMS = [
    TemplateParameter("batter_g", "continuous", "lognormal",
                      {"mu": 4.50, "sigma": 0.25}, unit="g", observable_from_image=True),
    TemplateParameter("rice_fraction", "continuous", "normal",
                      {"mu": 0.75, "sigma": 0.04}, unit="ratio"),
    TemplateParameter("fat_g", "continuous", "lognormal",
                      {"mu": 2.08, "sigma": 0.55}, unit="g",
                      elicitation_question="How much oil or ghee was used?"),
    TemplateParameter("fat_type", "categorical", "categorical",
                      {"categories": ["gingelly", "ghee"], "weights": [0.7, 0.3]}),
]

_BATTER_INGREDIENTS = [
    TemplateIngredient("batter_g * rice_fraction", food_id=RICE,
                       cooking_method="griddled", preparation_state="soaked_ground"),
    TemplateIngredient("batter_g * (1 - rice_fraction)", food_id=URAD,
                       cooking_method="griddled", preparation_state="soaked_ground"),
    TemplateIngredient('fat_g * (fat_type == "gingelly")', food_id=GINGELLY,
                       cooking_method="griddled"),
    TemplateIngredient('fat_g * (fat_type == "ghee")', food_id=GHEE,
                       cooking_method="griddled"),
]

PLAIN_DOSA = RecipeTemplate(
    template_id=1, pathyam_id="PY-T-000100", food_id=DOSA, base_method="griddled",
    parameters=_BATTER_PARAMS, ingredients=_BATTER_INGREDIENTS,
    default_servings=1.0, yield_factor=0.82,
)

POTATO_MASALA = RecipeTemplate(
    template_id=2, pathyam_id="PY-T-000102", food_id=FILLING, base_method="boiled",
    parameters=[
        TemplateParameter("potato_g", "continuous", "lognormal",
                          {"mu": 4.25, "sigma": 0.30}, unit="g", observable_from_image=True),
        TemplateParameter("onion_g", "continuous", "lognormal",
                          {"mu": 3.00, "sigma": 0.35}, unit="g", observable_from_image=True),
        TemplateParameter("masala_fat_g", "continuous", "lognormal",
                          {"mu": 1.61, "sigma": 0.45}, unit="g"),
    ],
    ingredients=[
        TemplateIngredient("potato_g", food_id=POTATO, cooking_method="boiled"),
        TemplateIngredient("onion_g", food_id=ONION, cooking_method="boiled"),
        TemplateIngredient("masala_fat_g", food_id=GINGELLY, cooking_method="boiled"),
    ],
    default_servings=1.0, yield_factor=0.95,
)

MASALA_DOSA_TEMPLATE = RecipeTemplate(
    template_id=3, pathyam_id="PY-T-000101", food_id=MASALA_DOSA, base_method="griddled",
    parameters=_BATTER_PARAMS + [
        TemplateParameter("filling_g", "continuous", "lognormal",
                          {"mu": 4.25, "sigma": 0.30}, unit="g", observable_from_image=True),
    ],
    ingredients=_BATTER_INGREDIENTS + [
        # The nested sub-recipe: filling_g is a TARGET MASS of potato masala.
        TemplateIngredient("filling_g", sub_template_id=2),
    ],
    default_servings=1.0, yield_factor=0.84,
)


@pytest.fixture
def repo() -> InMemoryRepository:
    composition = {
        food_id: [
            CompositionValue(food_id=food_id, nutrient_id=nid, value=val, sd=sd,
                             confidence="A", source_key="IFCT2017")
            for nid, val, sd in rows
        ]
        for food_id, rows in _COMP.items()
    }
    return InMemoryRepository(
        templates=[PLAIN_DOSA, POTATO_MASALA, MASALA_DOSA_TEMPLATE],
        nutrients=NUTRIENTS,
        composition=composition,
        food_meta=FOOD_META,
        retention=[
            RetentionFactor("cereal", "griddled", 6, 70.0, "USDA-NRF-R6"),
            RetentionFactor("pulse", "griddled", 6, 75.0, "USDA-NRF-R6"),
            RetentionFactor("vegetable", "boiled", 5, 70.0, "USDA-NRF-R6"),
        ],
        yields={("prepared_dish", "griddled"): 0.82},
        regional_priors={
            (1, "KL"): {"fat_type": ("categorical",
                                     {"categories": ["gingelly", "ghee"],
                                      "weights": [0.15, 0.85]})},
        },
        sources=[
            SourceRef("IFCT2017", "Longvah T et al. IFCT 2017.",
                      "Not stated - ICMR copyright", False),
            SourceRef("USDA-NRF-R6", "USDA Nutrient Retention Factors R6.",
                      "Public domain", True),
        ],
    )

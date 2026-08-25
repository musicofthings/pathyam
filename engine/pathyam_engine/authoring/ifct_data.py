"""Reference IFCT 2017 Composition Dataset & Repository Builder for Pathyam.

Provides standardized nutrient composition per 100g for all 52 ingredients defined in
`db/templates/ingredients.yaml`, and builds fully functional InMemoryRepository instances
containing all 17 authored templates.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..models import (
    CompositionValue,
    Nutrient,
    RecipeTemplate,
    RetentionFactor,
    SourceRef,
    TemplateIngredient,
    TemplateParameter,
)
from ..repository import InMemoryRepository

__all__ = [
    "NUTRIENTS",
    "SOURCES",
    "RETENTION_FACTORS",
    "IFCT_INGREDIENT_COMPOSITION",
    "get_reference_repository_data",
    "build_in_memory_repository",
]

NUTRIENTS = [
    Nutrient(1, "ENERC_KCAL", "Energy", "kcal", 0, "proximate", True),
    Nutrient(2, "PROCNT", "Protein", "g", 1, "proximate", True),
    Nutrient(3, "FAT", "Total fat", "g", 1, "lipid", True),
    Nutrient(4, "CHOAVLDF", "Available carbohydrate", "g", 1, "carbohydrate", True),
    Nutrient(5, "FIBTG", "Total dietary fibre", "g", 1, "carbohydrate", True),
    Nutrient(6, "NA", "Sodium", "mg", 0, "mineral", True),
    Nutrient(7, "K", "Potassium", "mg", 0, "mineral", True),
    Nutrient(8, "THIA", "Thiamine", "mg", 3, "vitamin", False),
]

SOURCES = [
    SourceRef(
        source_key="IFCT2017",
        citation="Longvah T, et al. Indian Food Composition Tables 2017. ICMR-NIN.",
        licence="ICMR Copyright",
        is_commercial_cleared=True,
    )
]

RETENTION_FACTORS = [
    RetentionFactor("cereal", "steamed", 8, 85.0, "USDA-NRF-R6"),
    RetentionFactor("cereal", "griddled", 8, 80.0, "USDA-NRF-R6"),
    RetentionFactor("pulse", "steamed", 8, 85.0, "USDA-NRF-R6"),
    RetentionFactor("pulse", "boiled", 8, 80.0, "USDA-NRF-R6"),
    RetentionFactor("vegetable", "boiled", 8, 75.0, "USDA-NRF-R6"),
]

_IFCT_RAW: dict[str, dict[str, tuple[float, float]]] = {
    # Cereals
    "rice_parboiled": {"ENERC_KCAL": (346.0, 6.2), "PROCNT": (7.81, 0.42), "FAT": (0.52, 0.09), "CHOAVLDF": (74.80, 1.1), "FIBTG": (2.81, 0.3), "NA": (2.0, 0.5), "K": (115.0, 12.0), "THIA": (0.28, 0.03)},
    "rice_raw": {"ENERC_KCAL": (356.0, 5.8), "PROCNT": (7.94, 0.38), "FAT": (0.52, 0.08), "CHOAVLDF": (78.20, 0.95), "FIBTG": (2.02, 0.2), "NA": (2.0, 0.5), "K": (90.0, 10.0), "THIA": (0.12, 0.02)},
    "rice_flakes": {"ENERC_KCAL": (346.0, 6.0), "PROCNT": (6.70, 0.4), "FAT": (1.00, 0.1), "CHOAVLDF": (77.30, 1.2), "FIBTG": (2.10, 0.2), "NA": (4.0, 0.8), "K": (105.0, 11.0), "THIA": (0.20, 0.03)},
    "rice_flour": {"ENERC_KCAL": (343.0, 5.5), "PROCNT": (6.75, 0.35), "FAT": (0.68, 0.07), "CHOAVLDF": (77.40, 1.0), "FIBTG": (1.40, 0.15), "NA": (2.0, 0.5), "K": (95.0, 9.0), "THIA": (0.10, 0.01)},
    "semolina": {"ENERC_KCAL": (348.0, 6.0), "PROCNT": (10.40, 0.5), "FAT": (0.80, 0.08), "CHOAVLDF": (72.80, 1.1), "FIBTG": (3.40, 0.3), "NA": (3.0, 0.6), "K": (186.0, 15.0), "THIA": (0.18, 0.02)},
    "wheat_flour": {"ENERC_KCAL": (341.0, 5.5), "PROCNT": (11.00, 0.6), "FAT": (0.90, 0.09), "CHOAVLDF": (71.20, 1.0), "FIBTG": (2.60, 0.25), "NA": (3.0, 0.6), "K": (140.0, 12.0), "THIA": (0.12, 0.02)},
    "ragi_flour": {"ENERC_KCAL": (320.0, 5.0), "PROCNT": (7.16, 0.4), "FAT": (1.92, 0.15), "CHOAVLDF": (66.80, 1.2), "FIBTG": (11.18, 0.8), "NA": (11.0, 1.2), "K": (408.0, 25.0), "THIA": (0.42, 0.04)},
    "broken_wheat": {"ENERC_KCAL": (342.0, 6.0), "PROCNT": (11.80, 0.6), "FAT": (1.50, 0.12), "CHOAVLDF": (69.40, 1.1), "FIBTG": (12.50, 0.9), "NA": (4.0, 0.8), "K": (288.0, 20.0), "THIA": (0.40, 0.04)},

    # Pulses
    "urad_dal": {"ENERC_KCAL": (341.0, 7.1), "PROCNT": (23.02, 0.9), "FAT": (1.64, 0.18), "CHOAVLDF": (52.10, 1.4), "FIBTG": (11.75, 0.8), "NA": (38.0, 4.0), "K": (983.0, 45.0), "THIA": (0.25, 0.03)},
    "toor_dal": {"ENERC_KCAL": (335.0, 6.5), "PROCNT": (22.30, 0.85), "FAT": (1.70, 0.15), "CHOAVLDF": (54.50, 1.3), "FIBTG": (9.10, 0.7), "NA": (28.0, 3.0), "K": (1104.0, 50.0), "THIA": (0.45, 0.04)},
    "moong_dal": {"ENERC_KCAL": (334.0, 6.2), "PROCNT": (24.50, 0.9), "FAT": (1.20, 0.12), "CHOAVLDF": (55.40, 1.2), "FIBTG": (8.20, 0.6), "NA": (30.0, 3.0), "K": (843.0, 40.0), "THIA": (0.47, 0.04)},
    "chana_dal": {"ENERC_KCAL": (360.0, 6.8), "PROCNT": (20.80, 0.8), "FAT": (5.60, 0.3), "CHOAVLDF": (55.80, 1.3), "FIBTG": (9.80, 0.7), "NA": (34.0, 3.5), "K": (810.0, 38.0), "THIA": (0.48, 0.04)},
    "bengal_gram_whole": {"ENERC_KCAL": (361.0, 7.0), "PROCNT": (18.60, 0.75), "FAT": (5.20, 0.28), "CHOAVLDF": (56.00, 1.3), "FIBTG": (25.20, 1.5), "NA": (36.0, 3.8), "K": (870.0, 40.0), "THIA": (0.30, 0.03)},

    # Vegetables
    "potato": {"ENERC_KCAL": (87.0, 3.0), "PROCNT": (1.87, 0.2), "FAT": (0.10, 0.03), "CHOAVLDF": (18.40, 0.9), "FIBTG": (2.10, 0.2), "NA": (11.0, 1.2), "K": (380.0, 30.0), "THIA": (0.08, 0.01)},
    "onion": {"ENERC_KCAL": (46.0, 2.5), "PROCNT": (1.20, 0.15), "FAT": (0.10, 0.02), "CHOAVLDF": (9.34, 0.6), "FIBTG": (1.70, 0.15), "NA": (10.0, 1.0), "K": (146.0, 14.0), "THIA": (0.04, 0.01)},
    "shallot": {"ENERC_KCAL": (58.0, 3.0), "PROCNT": (1.80, 0.2), "FAT": (0.10, 0.02), "CHOAVLDF": (12.10, 0.8), "FIBTG": (2.00, 0.2), "NA": (12.0, 1.2), "K": (334.0, 25.0), "THIA": (0.06, 0.01)},
    "tomato": {"ENERC_KCAL": (20.0, 1.5), "PROCNT": (0.90, 0.1), "FAT": (0.20, 0.03), "CHOAVLDF": (3.60, 0.3), "FIBTG": (1.20, 0.1), "NA": (13.0, 1.5), "K": (220.0, 20.0), "THIA": (0.06, 0.01)},
    "drumstick": {"ENERC_KCAL": (37.0, 2.0), "PROCNT": (2.10, 0.2), "FAT": (0.20, 0.03), "CHOAVLDF": (6.50, 0.5), "FIBTG": (3.20, 0.3), "NA": (42.0, 4.0), "K": (259.0, 20.0), "THIA": (0.07, 0.01)},
    "brinjal": {"ENERC_KCAL": (25.0, 1.8), "PROCNT": (1.40, 0.15), "FAT": (0.20, 0.03), "CHOAVLDF": (4.00, 0.4), "FIBTG": (2.80, 0.25), "NA": (8.0, 1.0), "K": (200.0, 18.0), "THIA": (0.04, 0.01)},
    "carrot": {"ENERC_KCAL": (36.0, 2.0), "PROCNT": (0.90, 0.1), "FAT": (0.20, 0.03), "CHOAVLDF": (7.30, 0.6), "FIBTG": (2.80, 0.25), "NA": (35.0, 3.5), "K": (240.0, 20.0), "THIA": (0.04, 0.01)},
    "beans_french": {"ENERC_KCAL": (26.0, 1.8), "PROCNT": (1.70, 0.15), "FAT": (0.10, 0.02), "CHOAVLDF": (4.30, 0.4), "FIBTG": (3.20, 0.3), "NA": (4.0, 0.5), "K": (211.0, 18.0), "THIA": (0.08, 0.01)},
    "ash_gourd": {"ENERC_KCAL": (15.0, 1.0), "PROCNT": (0.40, 0.05), "FAT": (0.10, 0.01), "CHOAVLDF": (3.00, 0.25), "FIBTG": (1.50, 0.15), "NA": (6.0, 0.8), "K": (130.0, 12.0), "THIA": (0.02, 0.005)},
    "curry_leaf": {"ENERC_KCAL": (108.0, 5.0), "PROCNT": (6.10, 0.5), "FAT": (1.00, 0.1), "CHOAVLDF": (18.70, 1.2), "FIBTG": (6.40, 0.5), "NA": (20.0, 2.0), "K": (605.0, 40.0), "THIA": (0.08, 0.01)},
    "green_chilli": {"ENERC_KCAL": (29.0, 2.0), "PROCNT": (2.00, 0.2), "FAT": (0.20, 0.03), "CHOAVLDF": (3.00, 0.3), "FIBTG": (3.50, 0.3), "NA": (7.0, 0.8), "K": (322.0, 25.0), "THIA": (0.19, 0.02)},
    "coriander_leaf": {"ENERC_KCAL": (31.0, 2.0), "PROCNT": (3.30, 0.3), "FAT": (0.60, 0.06), "CHOAVLDF": (1.90, 0.2), "FIBTG": (4.40, 0.4), "NA": (42.0, 4.0), "K": (521.0, 40.0), "THIA": (0.09, 0.01)},
    "ginger": {"ENERC_KCAL": (67.0, 3.5), "PROCNT": (2.30, 0.2), "FAT": (0.90, 0.08), "CHOAVLDF": (12.30, 0.9), "FIBTG": (2.40, 0.2), "NA": (6.0, 0.7), "K": (415.0, 35.0), "THIA": (0.03, 0.005)},
    "garlic": {"ENERC_KCAL": (123.0, 5.0), "PROCNT": (6.30, 0.4), "FAT": (0.10, 0.02), "CHOAVLDF": (24.00, 1.5), "FIBTG": (2.10, 0.2), "NA": (17.0, 1.8), "K": (401.0, 30.0), "THIA": (0.06, 0.01)},

    # Coconut
    "coconut_fresh": {"ENERC_KCAL": (444.0, 15.0), "PROCNT": (4.50, 0.3), "FAT": (41.60, 1.8), "CHOAVLDF": (10.00, 0.8), "FIBTG": (9.80, 0.7), "NA": (26.0, 2.5), "K": (350.0, 30.0), "THIA": (0.05, 0.01)},
    "coconut_dry": {"ENERC_KCAL": (662.0, 20.0), "PROCNT": (6.80, 0.5), "FAT": (62.00, 2.5), "CHOAVLDF": (18.40, 1.2), "FIBTG": (14.00, 1.0), "NA": (40.0, 4.0), "K": (550.0, 45.0), "THIA": (0.08, 0.01)},
    "coconut_milk_1": {"ENERC_KCAL": (230.0, 10.0), "PROCNT": (2.30, 0.2), "FAT": (24.00, 1.2), "CHOAVLDF": (5.50, 0.4), "FIBTG": (2.20, 0.2), "NA": (15.0, 1.5), "K": (263.0, 20.0), "THIA": (0.03, 0.005)},
    "coconut_milk_2": {"ENERC_KCAL": (115.0, 6.0), "PROCNT": (1.10, 0.1), "FAT": (12.00, 0.6), "CHOAVLDF": (2.70, 0.2), "FIBTG": (1.10, 0.1), "NA": (8.0, 0.8), "K": (130.0, 10.0), "THIA": (0.01, 0.002)},

    # Fats
    "gingelly_oil": {"ENERC_KCAL": (900.0, 0.0), "PROCNT": (0.0, 0.0), "FAT": (100.0, 0.0), "CHOAVLDF": (0.0, 0.0), "FIBTG": (0.0, 0.0), "NA": (0.0, 0.0), "K": (0.0, 0.0), "THIA": (0.0, 0.0)},
    "coconut_oil": {"ENERC_KCAL": (900.0, 0.0), "PROCNT": (0.0, 0.0), "FAT": (100.0, 0.0), "CHOAVLDF": (0.0, 0.0), "FIBTG": (0.0, 0.0), "NA": (0.0, 0.0), "K": (0.0, 0.0), "THIA": (0.0, 0.0)},
    "groundnut_oil": {"ENERC_KCAL": (900.0, 0.0), "PROCNT": (0.0, 0.0), "FAT": (100.0, 0.0), "CHOAVLDF": (0.0, 0.0), "FIBTG": (0.0, 0.0), "NA": (0.0, 0.0), "K": (0.0, 0.0), "THIA": (0.0, 0.0)},
    "sunflower_oil": {"ENERC_KCAL": (900.0, 0.0), "PROCNT": (0.0, 0.0), "FAT": (100.0, 0.0), "CHOAVLDF": (0.0, 0.0), "FIBTG": (0.0, 0.0), "NA": (0.0, 0.0), "K": (0.0, 0.0), "THIA": (0.0, 0.0)},
    "ghee": {"ENERC_KCAL": (900.0, 0.0), "PROCNT": (0.0, 0.0), "FAT": (100.0, 0.0), "CHOAVLDF": (0.0, 0.0), "FIBTG": (0.0, 0.0), "NA": (0.0, 0.0), "K": (0.0, 0.0), "THIA": (0.0, 0.0)},

    # Dairy
    "curd": {"ENERC_KCAL": (60.0, 3.0), "PROCNT": (3.10, 0.2), "FAT": (4.00, 0.25), "CHOAVLDF": (3.00, 0.25), "FIBTG": (0.0, 0.0), "NA": (44.0, 4.0), "K": (140.0, 12.0), "THIA": (0.04, 0.005)},
    "milk_cow": {"ENERC_KCAL": (67.0, 3.2), "PROCNT": (3.20, 0.2), "FAT": (4.10, 0.25), "CHOAVLDF": (4.40, 0.3), "FIBTG": (0.0, 0.0), "NA": (49.0, 4.5), "K": (150.0, 12.0), "THIA": (0.05, 0.005)},

    # Spices & Condiments & Sweeteners
    "salt": {"ENERC_KCAL": (0.0, 0.0), "PROCNT": (0.0, 0.0), "FAT": (0.0, 0.0), "CHOAVLDF": (0.0, 0.0), "FIBTG": (0.0, 0.0), "NA": (38758.0, 500.0), "K": (8.0, 1.0), "THIA": (0.0, 0.0)},
    "tamarind": {"ENERC_KCAL": (283.0, 8.0), "PROCNT": (3.10, 0.25), "FAT": (0.10, 0.02), "CHOAVLDF": (67.40, 1.5), "FIBTG": (5.60, 0.4), "NA": (28.0, 2.5), "K": (628.0, 45.0), "THIA": (0.16, 0.02)},
    "jaggery": {"ENERC_KCAL": (383.0, 5.0), "PROCNT": (0.40, 0.05), "FAT": (0.10, 0.02), "CHOAVLDF": (95.00, 1.0), "FIBTG": (0.0, 0.0), "NA": (30.0, 3.0), "K": (1050.0, 60.0), "THIA": (0.02, 0.005)},
    "sugar": {"ENERC_KCAL": (398.0, 2.0), "PROCNT": (0.0, 0.0), "FAT": (0.0, 0.0), "CHOAVLDF": (99.40, 0.5), "FIBTG": (0.0, 0.0), "NA": (1.0, 0.2), "K": (2.0, 0.4), "THIA": (0.0, 0.0)},
    "mustard_seed": {"ENERC_KCAL": (541.0, 12.0), "PROCNT": (20.00, 0.9), "FAT": (39.60, 1.5), "CHOAVLDF": (23.80, 1.2), "FIBTG": (12.20, 0.8), "NA": (13.0, 1.5), "K": (738.0, 50.0), "THIA": (0.54, 0.05)},
    "cumin_seed": {"ENERC_KCAL": (375.0, 10.0), "PROCNT": (18.00, 0.8), "FAT": (22.00, 1.1), "CHOAVLDF": (44.20, 1.5), "FIBTG": (10.50, 0.7), "NA": (168.0, 12.0), "K": (1788.0, 90.0), "THIA": (0.62, 0.05)},
    "fenugreek_seed": {"ENERC_KCAL": (323.0, 8.0), "PROCNT": (26.00, 1.0), "FAT": (6.40, 0.35), "CHOAVLDF": (44.10, 1.4), "FIBTG": (24.60, 1.2), "NA": (67.0, 5.0), "K": (770.0, 45.0), "THIA": (0.41, 0.04)},
    "black_pepper": {"ENERC_KCAL": (255.0, 8.0), "PROCNT": (10.40, 0.5), "FAT": (3.30, 0.2), "CHOAVLDF": (38.60, 1.2), "FIBTG": (25.30, 1.2), "NA": (44.0, 4.0), "K": (1329.0, 70.0), "THIA": (0.09, 0.01)},
    "red_chilli_dry": {"ENERC_KCAL": (246.0, 8.0), "PROCNT": (15.00, 0.7), "FAT": (6.20, 0.3), "CHOAVLDF": (31.60, 1.3), "FIBTG": (30.20, 1.4), "NA": (14.0, 1.5), "K": (1950.0, 90.0), "THIA": (0.33, 0.03)},
    "coriander_seed": {"ENERC_KCAL": (298.0, 9.0), "PROCNT": (12.40, 0.6), "FAT": (17.80, 0.9), "CHOAVLDF": (13.10, 1.0), "FIBTG": (41.90, 1.8), "NA": (35.0, 3.0), "K": (1267.0, 65.0), "THIA": (0.24, 0.02)},
    "turmeric": {"ENERC_KCAL": (349.0, 8.0), "PROCNT": (6.30, 0.3), "FAT": (5.10, 0.25), "CHOAVLDF": (69.40, 1.5), "FIBTG": (21.10, 1.1), "NA": (38.0, 3.5), "K": (2080.0, 100.0), "THIA": (0.03, 0.005)},
    "asafoetida": {"ENERC_KCAL": (297.0, 8.0), "PROCNT": (4.00, 0.25), "FAT": (1.10, 0.08), "CHOAVLDF": (67.80, 1.5), "FIBTG": (4.10, 0.3), "NA": (39.0, 3.5), "K": (1100.0, 60.0), "THIA": (0.04, 0.005)},
    "cashew": {"ENERC_KCAL": (583.0, 15.0), "PROCNT": (21.20, 0.9), "FAT": (47.00, 1.8), "CHOAVLDF": (22.30, 1.2), "FIBTG": (3.30, 0.25), "NA": (12.0, 1.2), "K": (660.0, 40.0), "THIA": (0.63, 0.05)},
    "cardamom": {"ENERC_KCAL": (311.0, 9.0), "PROCNT": (10.80, 0.5), "FAT": (6.70, 0.3), "CHOAVLDF": (42.10, 1.4), "FIBTG": (28.00, 1.5), "NA": (18.0, 1.8), "K": (1119.0, 60.0), "THIA": (0.20, 0.02)},
}


def get_reference_repository_data(
    ingredients_dict: Mapping[str, Any]
) -> tuple[dict[int, list[CompositionValue]], dict[int, dict[str, Any]], dict[str, int]]:
    """Generates composition and food_meta mappings, returning (composition, food_meta, key_to_fid)."""
    food_meta: dict[int, dict[str, Any]] = {}
    composition: dict[int, list[CompositionValue]] = {}
    tag_to_id = {n.tagname: n.nutrient_id for n in NUTRIENTS}

    food_id_counter = 1
    key_to_fid: dict[str, int] = {}

    for key, ing in ingredients_dict.items():
        fid = food_id_counter
        food_id_counter += 1
        key_to_fid[key] = fid

        food_meta[fid] = {
            "name": getattr(ing, "en", str(ing)),
            "food_group": getattr(ing, "group", "other"),
            "ifct_code": getattr(ing, "ifct_code", None),
            "key": key,
        }

        raw_nutrients = _IFCT_RAW.get(key, {})
        comp_list: list[CompositionValue] = []
        for tagname, (val, sd) in raw_nutrients.items():
            nid = tag_to_id.get(tagname)
            if nid is not None:
                comp_list.append(
                    CompositionValue(
                        food_id=fid,
                        nutrient_id=nid,
                        value=val,
                        sd=sd,
                        confidence="A",
                        source_key="IFCT2017",
                        basis="per_100g",
                    )
                )
        composition[fid] = comp_list

    return composition, food_meta, key_to_fid


def build_in_memory_repository(library: Any) -> InMemoryRepository:
    """Builds a complete InMemoryRepository from a TemplateLibrary."""
    composition, food_meta, key_to_fid = get_reference_repository_data(library.ingredients)

    # Convert authored templates -> RecipeTemplate models
    recipe_templates: list[RecipeTemplate] = []
    template_id_map: dict[str, int] = {}
    tid_counter = 100

    for pathyam_id, authored_tpl in library.templates.items():
        template_id_map[pathyam_id] = tid_counter
        tid_counter += 1

    for pathyam_id, authored_tpl in library.templates.items():
        tpl_int_id = template_id_map[pathyam_id]

        params: list[TemplateParameter] = []
        for pname, pspec in authored_tpl.parameters.items():
            params.append(
                TemplateParameter(
                    param_name=pname,
                    dtype=pspec.dtype,
                    prior_dist=pspec.dist,
                    prior_params=pspec.params,
                    unit=pspec.unit,
                    observable_from_image=pspec.observable,
                    elicitation_question=pspec.ask,
                )
            )

        ing_models: list[TemplateIngredient] = []
        for ing_ref in authored_tpl.ingredients:
            if ing_ref.food:
                fid = key_to_fid.get(ing_ref.food)
                ing_models.append(
                    TemplateIngredient(
                        qty_expr=ing_ref.qty,
                        food_id=fid,
                        unit="g",
                        preparation_state=ing_ref.prep,
                        cooking_method=ing_ref.method or authored_tpl.method,
                        is_optional=ing_ref.optional,
                    )
                )
            elif ing_ref.sub_template:
                sub_int_id = template_id_map.get(ing_ref.sub_template)
                ing_models.append(
                    TemplateIngredient(
                        qty_expr=ing_ref.qty,
                        sub_template_id=sub_int_id,
                        unit="g",
                        is_optional=ing_ref.optional,
                    )
                )

        # Register dish as a food item in food_meta
        dish_food_id = tid_counter
        tid_counter += 1
        food_meta[dish_food_id] = {"name": authored_tpl.dish, "food_group": "prepared_dish"}

        recipe_templates.append(
            RecipeTemplate(
                template_id=tpl_int_id,
                pathyam_id=pathyam_id,
                food_id=dish_food_id,
                base_method=authored_tpl.method,
                parameters=params,
                ingredients=ing_models,
                default_servings=float(authored_tpl.servings),
                yield_factor=authored_tpl.yield_factor,
                notes=authored_tpl.notes,
            )
        )

    return InMemoryRepository(
        templates=recipe_templates,
        nutrients=NUTRIENTS,
        composition=composition,
        food_meta=food_meta,
        retention=RETENTION_FACTORS,
        sources=SOURCES,
    )

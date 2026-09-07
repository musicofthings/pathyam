"""Tests for the IFCT 2017 ingest.

These use a hand-written fixture rather than the fetched table: the real CSV is
ICMR-NIN copyright and deliberately not committed (see scripts/fetch_ifct.py), so
the suite must not depend on it being present.

The fixture rows carry the same shape and units as the real file, including the
'Label; code' header convention and the `_e` standard-error columns.
"""

from __future__ import annotations

import textwrap

import pytest

from pathyam_engine.authoring.ifct2017 import (
    NUTRIENT_MAP,
    ZERO_IS_REAL,
    read_ifct_csv,
)

# Columns kept minimal but real: codes, units and the _e convention match the source.
_HEADER = (
    '"Food Code; code","Food Name; name","Scientific Name; scie","Local Name; lang",'
    '"Food Group; grup","No. of Regions; regn",'
    '"Energy; enerc","Energy; enerc_e","Total Fat; fatce","Total Fat; fatce_e",'
    '"Carbohydrate; choavldf","Carbohydrate; choavldf_e",'
    '"Protein; protcnt","Protein; protcnt_e",'
    '"Dietary Fiber; fibtg","Dietary Fiber; fibtg_e",'
    '"Potassium; k","Potassium; k_e","Vitamin C; vitc","Vitamin C; vitc_e"'
)


def _write(tmp_path, *rows: str):
    path = tmp_path / "compositions.csv"
    path.write_text(_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_units_are_converted_from_grams_and_kilojoules(tmp_path):
    """Source is g/100 g and kJ; ref.nutrient wants kcal, g, mg."""
    path = _write(
        tmp_path,
        '"A014","Rice, parboiled, milled","Oryza sativa",'
        '"Tam. Puzhungal arisi; Mal. Puzhungal ari.","Cereals and Millets",6,'
        '1471,8,0.55,0.08,77.16,0.76,7.81,0.63,3.74,0.36,0.142,0.0203,0.002,0.0001',
    )
    (food,) = list(read_ifct_csv(path))

    assert food.code == "A014"
    assert food.food_group == "cereal"          # mapped from the IFCT label
    assert food.n_regions == 6

    kcal, _ = food.values["ENERC_KCAL"]
    assert kcal == pytest.approx(1471 / 4.184, rel=1e-6)   # kJ -> kcal

    potassium, k_sd = food.values["K"]
    assert potassium == pytest.approx(142.0)               # g -> mg
    assert k_sd == pytest.approx(20.3)                     # the SD converts too

    vitc, _ = food.values["VITC"]
    assert vitc == pytest.approx(2.0)                      # 0.002 g -> 2 mg

    # Values that are already grams stay put.
    assert food.values["PROCNT"][0] == pytest.approx(7.81)
    assert food.values["FAT"][0] == pytest.approx(0.55)


def test_native_language_names_are_parsed_for_the_four_target_languages(tmp_path):
    path = _write(
        tmp_path,
        '"A014","Rice, parboiled, milled","Oryza sativa",'
        '"A. Ubha chaul; Tam. Puzhungal arisi; Tel. Uppudu biyyam; '
        'Mal. Puzhungal ari; Kan. Kusubalakki.","Cereals and Millets",6,'
        '1471,8,0.55,0.08,77.16,0.76,7.81,0.63,3.74,0.36,0.142,0.02,0.002,0.0001',
    )
    (food,) = list(read_ifct_csv(path))

    assert food.local_names == {
        "ta": "Puzhungal arisi",
        "te": "Uppudu biyyam",
        "ml": "Puzhungal ari",
        "kn": "Kusubalakki",
    }
    # Assamese is present in the source but not a language this product resolves.
    assert "as" not in food.local_names


def test_a_zero_is_dropped_unless_zero_is_physically_real(tmp_path):
    """The source cannot distinguish 'not reported' from a measured zero."""
    path = _write(
        tmp_path,
        # An oil: fat 100 g is real, carbohydrate 0 is real, but potassium 0 and
        # vitamin C 0 mean "not measured" and must not enter as false zeros.
        '"T001","Coconut oil","","","Edible Oils and Fats",1,'
        '0,0,100,0,0,0,0,0,0,0,0,0,0,0',
    )
    (food,) = list(read_ifct_csv(path))

    assert food.values["FAT"][0] == pytest.approx(100.0)
    assert "CHOAVLDF" in food.values and food.values["CHOAVLDF"][0] == 0.0
    assert "K" not in food.values, "a zero mineral is 'not reported', not zero"
    assert "VITC" not in food.values

    assert "CHOAVLDF" in ZERO_IS_REAL and "K" not in ZERO_IS_REAL


def test_energy_is_derived_when_the_source_reports_none(tmp_path):
    """IFCT leaves energy blank for the pure fats; Atwater over the macros is exact."""
    path = _write(
        tmp_path,
        '"T001","Coconut oil","","","Edible Oils and Fats",1,'
        '0,0,100,0,0,0,0,0,0,0,0,0,0,0',
    )
    (food,) = list(read_ifct_csv(path))

    assert "ENERC_KCAL" in food.values
    assert food.values["ENERC_KCAL"][0] == pytest.approx(900.0)   # 100 g x 9 kcal
    assert food.values["ENERC_KCAL"][1] is None                   # calculated: no SD
    assert "ENERC_KCAL" in food.derived, "a calculated value must be marked as such"


def test_reported_energy_is_never_overwritten_by_a_derived_one(tmp_path):
    path = _write(
        tmp_path,
        '"A014","Rice, parboiled, milled","","","Cereals and Millets",6,'
        '1471,8,0.55,0.08,77.16,0.76,7.81,0.63,3.74,0.36,0.142,0.02,0.002,0.0001',
    )
    (food,) = list(read_ifct_csv(path))

    assert food.derived == frozenset()
    assert food.values["ENERC_KCAL"][0] == pytest.approx(1471 / 4.184)


def test_every_mapped_nutrient_uses_an_infoods_tagname():
    """ref.nutrient forbids invented local codes; guard the mapping against drift."""
    for ifct_code, (tag, name, unit, group, _core, factor) in NUTRIENT_MAP.items():
        assert tag.isupper(), f"{ifct_code}: {tag} is not an INFOODS-style tagname"
        assert unit in {"kcal", "g", "mg", "ug"}, f"{tag}: unexpected unit {unit}"
        assert group in {
            "proximate", "carbohydrate", "lipid", "mineral", "vitamin",
            "amino_acid", "fatty_acid", "bioactive", "other",
        }, f"{tag}: {group} is not a ref.nutrient group"
        assert factor > 0

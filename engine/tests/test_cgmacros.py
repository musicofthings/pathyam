"""CGMacros ingest: parsing, and the guarantees that keep licensed data separable.

Fixture-based. CGMacros is CC BY-NC-SA and is fetched, never vendored, so these
tests synthesise CSVs with the real column names from the published data dictionary
(DataDictionary_CGMacros-00X.csv and DataDictionary_Bio.csv) rather than reading the
dataset itself.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pathyam_engine.authoring.cgmacros import (
    POPULATION_NOTE,
    SOURCE_KEY,
    _glycaemic_status,
    read_bio_csv,
    read_participant_csv,
)

# Verbatim header from DataDictionary_CGMacros-00X.csv.
_PARTICIPANT_HEADER = (
    "Timestamp,Libre GL,Dexcom GL,HR,Calories (Activity),Mets,Meal Type,"
    "Calories,Carbs,Protein,Fat,Fiber,Amount Consumed,Image Path"
)


def _participant_csv(tmp_path, rows: list[str], name="CGMacros-001.csv"):
    path = tmp_path / name
    path.write_text(_PARTICIPANT_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------- readings ----

def test_blank_glucose_cells_are_not_read_as_zero(tmp_path):
    """The CSV is a one-minute grid; Libre samples every 15 min and Dexcom every 5.

    Most glucose cells are therefore blank. Reading a blank as 0.0 would inject
    thousands of impossible readings and flatten every curve — and 0 is below the
    CHECK constraint, so it would fail loudly only after the parse.
    """
    path = _participant_csv(tmp_path, [
        "01/02/2021 08:00,105,102,60,1.0,10,,,,,,,,",
        "01/02/2021 08:01,,,61,1.0,10,,,,,,,,",
        "01/02/2021 08:02,,104,62,1.0,10,,,,,,,,",
    ])
    readings, _, _ = read_participant_csv(path)

    assert len(readings) == 3, "one Libre + two Dexcom, and nothing from the blanks"
    assert all(r.glucose_mg_dl >= 40 for r in readings)


def test_the_two_sensors_are_kept_apart_never_averaged(tmp_path):
    """A Libre Pro and a Dexcom G6 Pro were worn simultaneously and disagree.

    Averaging them, or storing one under a generic name, means any model fitted
    afterwards is partly fitting sensor bias.
    """
    path = _participant_csv(tmp_path, [
        "01/02/2021 08:00,120,138,60,1.0,10,,,,,,,,",
    ])
    readings, _, _ = read_participant_csv(path)

    by_sensor = {r.sensor: r.glucose_mg_dl for r in readings}
    assert by_sensor == {"FreeStyle Libre Pro": 120.0, "Dexcom G6 Pro": 138.0}


def test_a_reading_outside_the_sensor_range_is_dropped_not_clamped(tmp_path):
    """Clamping to the boundary would invent a measurement at 40 or 450."""
    path = _participant_csv(tmp_path, [
        "01/02/2021 08:00,12,102,60,1.0,10,,,,,,,,",
    ])
    readings, _, warnings = read_participant_csv(path)

    assert [r.sensor for r in readings] == ["Dexcom G6 Pro"]
    assert any("outside sensor range" in w for w in warnings)


# ------------------------------------------------------------------ meals ----

def test_a_meal_row_is_parsed_with_its_macros(tmp_path):
    path = _participant_csv(tmp_path, [
        "01/02/2021 12:30,140,145,70,2.0,12,Lunch,620,75.5,28,22,9,100,photos/1.jpg",
    ])
    _, meals, _ = read_participant_csv(path)

    assert len(meals) == 1
    meal = meals[0]
    assert meal.meal_type == "lunch"
    assert (meal.energy_kcal, meal.carbohydrate_g, meal.protein_g) == (620.0, 75.5, 28.0)
    assert meal.fibre_g == 9.0
    assert meal.photo_ref == "photos/1.jpg"
    assert meal.consumed_at == dt.datetime(2021, 1, 2, 12, 30, tzinfo=dt.timezone.utc)


def test_amount_consumed_becomes_a_fraction_and_absent_is_not_all_of_it(tmp_path):
    """'Amount Consumed' is a percentage estimated from photographs.

    A blank means the dataset did not record it. Defaulting that to 100% would
    assert the participant finished a meal nobody measured.
    """
    path = _participant_csv(tmp_path, [
        "01/02/2021 12:30,140,145,70,2.0,12,Lunch,620,75,28,22,9,75,photos/1.jpg",
        "01/02/2021 19:00,150,152,70,2.0,12,Dinner,700,80,30,25,10,,photos/2.jpg",
    ])
    _, meals, _ = read_participant_csv(path)

    assert meals[0].fraction_consumed == 0.75
    assert meals[1].fraction_consumed is None, "unmeasured is not 1.0"


def test_rows_without_a_meal_type_do_not_produce_meals(tmp_path):
    path = _participant_csv(tmp_path, [
        "01/02/2021 08:00,105,102,60,1.0,10,,,,,,,,",
        "01/02/2021 12:30,140,145,70,2.0,12,Lunch,620,75,28,22,9,100,p.jpg",
    ])
    _, meals, _ = read_participant_csv(path)
    assert len(meals) == 1


# ---------------------------------------------------------------- subjects ----

_BIO_HEADER = (
    "Age,Gender,BMI,Body weight ,Height ,Self-identify ,A1c PDL (Lab),"
    "Fasting GLU - PDL (Lab),Insulin "
)


def test_bio_demographics_are_read_with_ethnicity_kept_verbatim(tmp_path):
    """The categories a study used are part of what its results mean.

    Remapping 'Hispanic/Latino' into our own vocabulary would quietly discard the
    fact that this cohort has no South Asian participants — the single most
    important thing to know before fitting anything on it.
    """
    path = tmp_path / "bio.csv"
    path.write_text(
        _BIO_HEADER + "\n"
        "34,F,27.4,168.0,65,Hispanic/Latino,5.9,104,12.1\n"
        "58,M,31.2,210.0,70,White,7.1,141,22.0\n",
        encoding="utf-8",
    )
    subjects = read_bio_csv(path)

    assert set(subjects) == {"1", "2"}
    assert subjects["1"].self_identified_ethnicity == "Hispanic/Latino"
    assert subjects["1"].sex == "F"
    assert subjects["1"].age_years == 34


@pytest.mark.parametrize("hba1c,fasting,expected", [
    (5.2, 88, "normoglycaemic"),
    (5.9, 104, "prediabetes"),
    (7.1, 141, "type_2_diabetes"),
    (None, 88, "normoglycaemic"),
    (None, 130, "type_2_diabetes"),
    (None, None, "unknown"),
])
def test_glycaemic_status_is_derived_from_each_subject_not_assigned_to_fit_totals(
    hba1c, fasting, expected
):
    """The paper publishes 15/16/14 group sizes but not per-subject labels.

    Assigning subjects to groups to reproduce those counts would be fabrication.
    Each subject's status comes from their own HbA1c or fasting glucose.
    """
    assert _glycaemic_status(hba1c, fasting) == expected


# ------------------------------------------------------------- provenance ----

def test_the_population_note_records_what_disqualifies_this_cohort():
    """It is stored on the dataset row so it cannot be lost between here and a fit."""
    note = POPULATION_NOTE.lower()
    assert "santa barbara" in note
    assert "none south asian" in note
    assert "no south indian food" in note
    assert "not weighed" in note
    assert "date-shifted" in note
    assert SOURCE_KEY == "CGMACROS-1.0.0"

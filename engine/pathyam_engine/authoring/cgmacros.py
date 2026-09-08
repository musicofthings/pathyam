"""Ingest the CGMacros research cohort into the ``research`` schema.

    PYTHONPATH=. python3 -m pathyam_engine.authoring cgmacros --dir .cgmacros

WHAT THIS DATA IS FOR
---------------------
Exercising the postprandial pipeline against real traces instead of none: ingestion,
meal/reading pairing, and the shape of a fitting routine. Until this exists,
``app.v_postprandial_reading`` has never been run against anything.

WHAT IT IS NOT FOR
------------------
**Fitting the coefficients that ship.** The cohort is 45 adults recruited in Santa
Barbara, California -- 34 of 45 self-identified Hispanic/Latino, none South Asian --
eating protein shakes and Mexican-chain lunches. Postprandial glycaemic response
transfers across neither population nor cuisine, and Pathyam's users are South
Indian. Coefficients fitted here would be exactly the kind of confident, unsourced
number this codebase spent six phases removing; they would simply have a citation
attached, which is worse, not better.

**Standing in for the golden meal dataset.** CGMacros reports "Amount Consumed --
Estimate of % of meal consumed", read off before/after photographs. Nothing was
weighed. ``research.dataset.portions_are_weighed`` is written FALSE for this reason.

LICENCE
-------
CC BY-NC-SA 4.0. NonCommercial, and ShareAlike may extend to anything fitted on it.
The source row is written with ``is_commercial_cleared = false`` and is never
flipped here, so the dataset shows in ``ref.v_release_blockers``.

TWO DATA QUIRKS THAT MATTER
---------------------------
*Timestamps are randomly date-shifted* by up to a year. Intervals within a subject
survive; absolute dates and seasonality do not. Recorded on the dataset row.

*Two sensors per subject.* Each row of the per-participant CSV carries a `Libre GL`
and a `Dexcom GL` column, from a FreeStyle Libre Pro (15-min) and a Dexcom G6 Pro
(5-min) worn simultaneously. They are stored as separate rows with the sensor named,
never averaged: the two disagree by a clinically meaningful margin, and a model
fitted across both without distinguishing them is partly fitting sensor bias.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "CGMacrosReading",
    "CGMacrosMeal",
    "CGMacrosSubject",
    "CGMacrosLoadResult",
    "read_participant_csv",
    "read_bio_csv",
    "load_cgmacros_into_postgres",
    "SOURCE_KEY",
    "DATASET_KEY",
]

SOURCE_KEY = "CGMACROS-1.0.0"
DATASET_KEY = "CGMACROS-1.0.0"

_CITATION = (
    "Das S, Kerr D, et al. CGMacros: a pilot scientific dataset for personalized "
    "nutrition and diet monitoring. Scientific Data (2025). PhysioNet v1.0.0."
)
_URL = "https://physionet.org/content/cgmacros/1.0.0/"
_LICENCE = "CC BY-NC-SA 4.0 (NonCommercial, ShareAlike)"

POPULATION_NOTE = (
    "45 adults recruited at Sansum Diabetes Research Institute, Santa Barbara, "
    "California, 2021-2024 (15 normoglycaemic, 16 prediabetes, 14 type 2 diabetes). "
    "Self-identified ethnicity: 34 Hispanic/Latino, 7 White, 4 African American; "
    "none South Asian. Standardised breakfasts were protein shakes and lunches were "
    "ordered from a Mexican restaurant chain; dinners self-selected. No South Indian "
    "food. Portions estimated from before/after photographs as a percentage "
    "consumed, not weighed. Timestamps randomly date-shifted."
)

# Sensor column -> the name stored in research.cgm_reading.sensor.
_SENSOR_COLUMNS = {
    "Libre GL": "FreeStyle Libre Pro",
    "Dexcom GL": "Dexcom G6 Pro",
}

# The CHECK on research.cgm_reading. The data dictionary states the sensors report
# 40-400; anything outside is a sensor error, not a measurement, and is dropped
# rather than clamped -- clamping would invent a reading at the boundary.
_GLUCOSE_MIN, _GLUCOSE_MAX = 40.0, 450.0

_TIMESTAMP_FORMATS = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class CGMacrosReading:
    reading_at: dt.datetime
    glucose_mg_dl: float
    sensor: str


@dataclass(frozen=True)
class CGMacrosMeal:
    consumed_at: dt.datetime
    meal_type: str | None
    energy_kcal: float | None
    carbohydrate_g: float | None
    protein_g: float | None
    fat_g: float | None
    fibre_g: float | None
    fraction_consumed: float | None
    photo_ref: str | None


@dataclass
class CGMacrosSubject:
    external_ref: str
    age_years: int | None = None
    sex: str | None = None
    self_identified_ethnicity: str | None = None
    bmi: float | None = None
    glycaemic_status: str | None = None
    hba1c_percent: float | None = None
    fasting_glucose_mg_dl: float | None = None


@dataclass
class CGMacrosLoadResult:
    subjects: int = 0
    readings: int = 0
    meals: int = 0
    skipped_readings: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subjects": self.subjects,
            "readings": self.readings,
            "meals": self.meals,
            "skipped_readings": self.skipped_readings,
            "warnings": self.warnings,
        }


# ------------------------------------------------------------------ parsing --

def _num(raw: Any) -> float | None:
    """A blank cell is 'not measured', not zero.

    The per-participant CSV is a one-minute grid, but the Libre samples every 15
    minutes and the Dexcom every 5, so most glucose cells are blank. Reading a blank
    as 0.0 would inject thousands of impossible readings and flatten every curve.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.upper() in {"NA", "N/A", "NAN", "NULL", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_timestamp(raw: str) -> dt.datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _clean_header(name: str) -> str:
    # The shipped CSVs carry a BOM and inconsistent trailing spaces ("Body weight ").
    return (name or "").replace("﻿", "").strip()


def read_participant_csv(
    path: str | Path,
) -> tuple[list[CGMacrosReading], list[CGMacrosMeal], list[str]]:
    """Split one CGMacros-0XX.csv into readings and meals.

    Returns ``(readings, meals, warnings)``. A row carries a meal when `Meal Type`
    is populated; glucose columns are independent of that and are read from every
    row that has them.
    """
    readings: list[CGMacrosReading] = []
    meals: list[CGMacrosMeal] = []
    warnings: list[str] = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [_clean_header(f) for f in (reader.fieldnames or [])]
        present = set(reader.fieldnames)

        missing_sensors = [c for c in _SENSOR_COLUMNS if c not in present]
        if len(missing_sensors) == len(_SENSOR_COLUMNS):
            warnings.append(f"{Path(path).name}: no glucose column found")

        for row in reader:
            row = {_clean_header(k): v for k, v in row.items()}
            when = _parse_timestamp(row.get("Timestamp", ""))
            if when is None:
                continue

            for column, sensor in _SENSOR_COLUMNS.items():
                value = _num(row.get(column))
                if value is None:
                    continue
                if not (_GLUCOSE_MIN <= value <= _GLUCOSE_MAX):
                    # Out of the sensor's own reporting range: an error, not a
                    # measurement. Dropped, and counted, never clamped.
                    warnings.append(
                        f"{Path(path).name}: {sensor} {value} mg/dL at {when} "
                        "outside sensor range, dropped"
                    )
                    continue
                readings.append(CGMacrosReading(when, value, sensor))

            meal_type = (row.get("Meal Type") or "").strip()
            if meal_type:
                consumed = _num(row.get("Amount Consumed"))
                meals.append(CGMacrosMeal(
                    consumed_at=when,
                    meal_type=meal_type.lower(),
                    energy_kcal=_num(row.get("Calories")),
                    carbohydrate_g=_num(row.get("Carbs")),
                    protein_g=_num(row.get("Protein")),
                    fat_g=_num(row.get("Fat")),
                    fibre_g=_num(row.get("Fiber")),
                    # Reported as a percentage; stored as a fraction. NULL when the
                    # dataset did not record it, which is not the same as "all of it".
                    fraction_consumed=None if consumed is None else round(consumed / 100.0, 3),
                    photo_ref=(row.get("Image Path") or "").strip() or None,
                ))

    return readings, meals, warnings


_SEX_MAP = {"F": "F", "M": "M", "FEMALE": "F", "MALE": "M"}


def _glycaemic_status(hba1c: float | None, fasting: float | None) -> str:
    """ADA thresholds. The dataset states group sizes but not per-subject labels.

    Derived, not asserted: 15/16/14 is published for the cohort, but which subject
    is in which group is not, so this is computed from each subject's own HbA1c and
    fasting glucose rather than assigned to match the published counts.
    """
    if hba1c is not None:
        if hba1c >= 6.5:
            return "type_2_diabetes"
        if hba1c >= 5.7:
            return "prediabetes"
        return "normoglycaemic"
    if fasting is not None:
        if fasting >= 126:
            return "type_2_diabetes"
        if fasting >= 100:
            return "prediabetes"
        return "normoglycaemic"
    return "unknown"


def read_bio_csv(path: str | Path) -> dict[str, CGMacrosSubject]:
    """Per-subject demographics from bio.csv, keyed by participant number."""
    out: dict[str, CGMacrosSubject] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [_clean_header(f) for f in (reader.fieldnames or [])]
        for index, row in enumerate(reader, start=1):
            row = {_clean_header(k): v for k, v in row.items()}
            ref = (row.get("subject") or row.get("Subject")
                   or row.get("participant") or str(index)).strip()
            hba1c = _num(row.get("A1c PDL (Lab)"))
            fasting = _num(row.get("Fasting GLU - PDL (Lab)"))
            sex_raw = (row.get("Gender") or "").strip().upper()
            out[ref] = CGMacrosSubject(
                external_ref=ref,
                age_years=int(a) if (a := _num(row.get("Age"))) is not None else None,
                sex=_SEX_MAP.get(sex_raw, "unknown"),
                # Verbatim from the source. The categories a study used are part of
                # what its results mean, so they are not remapped to our vocabulary.
                self_identified_ethnicity=(row.get("Self-identify") or "").strip() or None,
                bmi=_num(row.get("BMI")),
                glycaemic_status=_glycaemic_status(hba1c, fasting),
                hba1c_percent=hba1c,
                fasting_glucose_mg_dl=fasting,
            )
    return out


def _participant_number(path: Path) -> str:
    match = re.search(r"(\d+)", path.stem)
    return str(int(match.group(1))) if match else path.stem


def iter_participant_files(root: str | Path) -> Iterator[Path]:
    """Every CGMacros-0XX.csv under ``root``, in participant order."""
    base = Path(root)
    files = [p for p in base.rglob("CGMacros-*.csv")
             if not p.name.startswith("DataDictionary")]
    return iter(sorted(files, key=lambda p: int(_participant_number(p))))


# ------------------------------------------------------------------ loading --

def load_cgmacros_into_postgres(
    root: str | Path,
    conn,
    *,
    dry_run: bool = False,
    limit_subjects: int | None = None,
) -> CGMacrosLoadResult:
    """Load the cohort into the ``research`` schema.

    The source row is written with ``is_commercial_cleared = false`` and is never
    flipped to true here. CGMacros is CC BY-NC-SA: NonCommercial reuse only, with
    ShareAlike that may extend to anything fitted on it. ``ref.v_release_blockers``
    is the gate that depends on the flag.
    """
    root = Path(root)
    result = CGMacrosLoadResult()

    bio_path = next(iter(root.rglob("bio.csv")), None)
    subjects = read_bio_csv(bio_path) if bio_path else {}
    if bio_path is None:
        result.warnings.append("bio.csv not found; subjects will carry no demographics")

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ref.source
                   (source_key, citation, doi_or_url, licence, is_commercial_cleared, notes)
               VALUES (%s, %s, %s, %s, false, %s)
               ON CONFLICT (source_key) DO UPDATE
                   SET citation = EXCLUDED.citation,
                       doi_or_url = EXCLUDED.doi_or_url,
                       licence = EXCLUDED.licence,
                       notes = EXCLUDED.notes
               RETURNING source_id""",
            (
                SOURCE_KEY, _CITATION, _URL, _LICENCE,
                "NONCOMMERCIAL LICENCE. Development fixture only — exercises the "
                "postprandial pipeline. Must not be used to fit shipped coefficients: "
                "the cohort is Californian and the meals are not South Indian. "
                "Deliberately uncleared so it shows in ref.v_release_blockers.",
            ),
        )
        source_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO research.dataset
                   (dataset_key, source_id, population_note, portions_are_weighed,
                    timestamps_are_shifted, subject_count, retrieved_on)
               VALUES (%s, %s, %s, false, true, %s, current_date)
               ON CONFLICT (dataset_key) DO UPDATE
                   SET population_note = EXCLUDED.population_note,
                       subject_count = EXCLUDED.subject_count,
                       retrieved_on = EXCLUDED.retrieved_on
               RETURNING dataset_id""",
            (DATASET_KEY, source_id, POPULATION_NOTE, len(subjects) or None),
        )
        dataset_id = cur.fetchone()[0]

        files = list(iter_participant_files(root))
        if limit_subjects is not None:
            files = files[:limit_subjects]
        if not files:
            result.warnings.append(f"no CGMacros-*.csv files under {root}")

        for path in files:
            ref = _participant_number(path)
            meta = subjects.get(ref, CGMacrosSubject(external_ref=ref))

            cur.execute(
                """INSERT INTO research.subject
                       (dataset_id, external_ref, age_years, sex,
                        self_identified_ethnicity, bmi, glycaemic_status,
                        hba1c_percent, fasting_glucose_mg_dl)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (dataset_id, external_ref) DO UPDATE
                       SET age_years = EXCLUDED.age_years,
                           sex = EXCLUDED.sex,
                           self_identified_ethnicity = EXCLUDED.self_identified_ethnicity,
                           bmi = EXCLUDED.bmi,
                           glycaemic_status = EXCLUDED.glycaemic_status,
                           hba1c_percent = EXCLUDED.hba1c_percent,
                           fasting_glucose_mg_dl = EXCLUDED.fasting_glucose_mg_dl
                   RETURNING subject_id""",
                (dataset_id, ref, meta.age_years, meta.sex,
                 meta.self_identified_ethnicity, meta.bmi, meta.glycaemic_status,
                 meta.hba1c_percent, meta.fasting_glucose_mg_dl),
            )
            subject_id = cur.fetchone()[0]
            result.subjects += 1

            readings, meals, warnings = read_participant_csv(path)
            result.warnings.extend(warnings[:5])
            result.skipped_readings += sum(1 for w in warnings if "outside sensor range" in w)

            # Idempotent on the natural key: a re-run updates rather than
            # duplicating. A doubled reading would silently bias any fitted curve,
            # which is the failure the IFCT loader hit across a date boundary.
            cur.executemany(
                """INSERT INTO research.cgm_reading
                       (subject_id, reading_at, glucose_mg_dl, sensor)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (subject_id, reading_at, sensor) DO UPDATE
                       SET glucose_mg_dl = EXCLUDED.glucose_mg_dl""",
                [(subject_id, r.reading_at, r.glucose_mg_dl, r.sensor) for r in readings],
            )
            result.readings += len(readings)

            cur.executemany(
                """INSERT INTO research.meal
                       (subject_id, consumed_at, meal_type, energy_kcal,
                        carbohydrate_g, protein_g, fat_g, fibre_g,
                        fraction_consumed, photo_ref)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (subject_id, consumed_at) DO UPDATE
                       SET meal_type = EXCLUDED.meal_type,
                           energy_kcal = EXCLUDED.energy_kcal,
                           carbohydrate_g = EXCLUDED.carbohydrate_g,
                           protein_g = EXCLUDED.protein_g,
                           fat_g = EXCLUDED.fat_g,
                           fibre_g = EXCLUDED.fibre_g,
                           fraction_consumed = EXCLUDED.fraction_consumed,
                           photo_ref = EXCLUDED.photo_ref""",
                [(subject_id, m.consumed_at, m.meal_type, m.energy_kcal,
                  m.carbohydrate_g, m.protein_g, m.fat_g, m.fibre_g,
                  m.fraction_consumed, m.photo_ref) for m in meals],
            )
            result.meals += len(meals)

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result

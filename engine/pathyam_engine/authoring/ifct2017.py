"""Ingest the real ICMR-NIN IFCT 2017 tables into Postgres.

Replaces hand-entered approximations with the published table: 528 foods, real
IFCT food codes, scientific names, per-value standard errors, and the native-language
food names IFCT carries for Tamil, Telugu, Malayalam and Kannada.

Run `scripts/fetch_ifct.py` first -- the CSV is not vendored (see that file for why).

UNITS
-----
The source expresses every mass-based nutrient as **grams per 100 g** and energy as
**kilojoules per 100 g**. That is not documented in the package, so it was verified
against reference values before this mapping was written:

    Guava vitamin C   0.214   g -> 214 mg     (published ~200 mg/100 g)
    Egg cholesterol   0.366   g -> 366 mg     (published ~370 mg/100 g)
    Amaranth leaf Fe  0.00464 g -> 4.64 mg    (published ~4-5 mg/100 g)
    Coconut oil fat   100     g               (definitionally 100)
    Rice parboiled    1471    kJ -> 351.6 kcal

`factor` below converts the source's grams into the unit `ref.nutrient` declares.

MISSING VALUES
--------------
The source uses 0 for "not reported" as well as for a genuine zero, and the two are
indistinguishable in the file. Rather than load a false zero into a clinical
database, a 0 is skipped for every nutrient except those on ZERO_IS_REAL, where a
zero is a physical fact (no carbohydrate in oil, no cholesterol in a plant food).
A skipped nutrient is absent, and the engine's composition-coverage QC gate reports
it -- which is the honest outcome.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "NUTRIENT_MAP",
    "IFCTFood",
    "read_ifct_csv",
    "load_ifct_into_postgres",
    "IFCTLoadResult",
]

_KJ_PER_KCAL = 4.184

# IFCT column code -> (INFOODS tagname, display name, unit, group, is_core, factor)
#
# `factor` multiplies the source value (grams, or kJ for energy) to reach `unit`.
# Tagnames are FAO/INFOODS; do not invent local codes -- ref.nutrient says so, and
# retrofitting identifiers across a populated composition table is a rewrite.
NUTRIENT_MAP: dict[str, tuple[str, str, str, str, bool, float]] = {
    # ---- proximates -------------------------------------------------------
    "enerc":    ("ENERC_KCAL", "Energy",                 "kcal", "proximate",    True,  1.0 / _KJ_PER_KCAL),
    "protcnt":  ("PROCNT",     "Protein",                "g",    "proximate",    True,  1.0),
    "fatce":    ("FAT",        "Total fat",              "g",    "lipid",        True,  1.0),
    "choavldf": ("CHOAVLDF",   "Available carbohydrate", "g",    "carbohydrate", True,  1.0),
    "fibtg":    ("FIBTG",      "Total dietary fibre",    "g",    "carbohydrate", True,  1.0),
    "water":    ("WATER",      "Moisture",               "g",    "proximate",    False, 1.0),
    "ash":      ("ASH",        "Ash",                    "g",    "proximate",    False, 1.0),

    # ---- carbohydrate fractions ------------------------------------------
    "starch":   ("STARCH",     "Starch",                 "g",    "carbohydrate", False, 1.0),
    "fsugar":   ("SUGAR",      "Total free sugars",      "g",    "carbohydrate", True,  1.0),
    "fibins":   ("FIBINS",     "Insoluble dietary fibre","g",    "carbohydrate", False, 1.0),
    "fibsol":   ("FIBSOL",     "Soluble dietary fibre",  "g",    "carbohydrate", False, 1.0),

    # ---- lipid fractions --------------------------------------------------
    "fasat":    ("FASAT",      "Saturated fatty acids",  "g",    "fatty_acid",   False, 1.0),
    "fams":     ("FAMS",       "Monounsaturated fatty acids", "g", "fatty_acid", False, 1.0),
    "fapu":     ("FAPU",       "Polyunsaturated fatty acids", "g", "fatty_acid", False, 1.0),
    "fatrn":    ("FATRN",      "Trans fatty acids",      "g",    "fatty_acid",   False, 1.0),
    "cholc":    ("CHOLE",      "Cholesterol",            "mg",   "lipid",        False, 1000.0),

    # ---- minerals (source grams -> mg, except selenium -> ug) -------------
    "ca":       ("CA",         "Calcium",                "mg",   "mineral",      True,  1000.0),
    "p":        ("P",          "Phosphorus",             "mg",   "mineral",      False, 1000.0),
    "mg":       ("MG",         "Magnesium",              "mg",   "mineral",      False, 1000.0),
    "na":       ("NA",         "Sodium",                 "mg",   "mineral",      True,  1000.0),
    "k":        ("K",          "Potassium",              "mg",   "mineral",      True,  1000.0),
    "fe":       ("FE",         "Iron",                   "mg",   "mineral",      True,  1000.0),
    "zn":       ("ZN",         "Zinc",                   "mg",   "mineral",      True,  1000.0),
    "cu":       ("CU",         "Copper",                 "mg",   "mineral",      False, 1000.0),
    "mn":       ("MN",         "Manganese",              "mg",   "mineral",      False, 1000.0),
    "se":       ("SE",         "Selenium",               "ug",   "mineral",      False, 1_000_000.0),

    # ---- vitamins ---------------------------------------------------------
    "thia":     ("THIA",       "Thiamine (B1)",          "mg",   "vitamin",      True,  1000.0),
    "ribf":     ("RIBF",       "Riboflavin (B2)",        "mg",   "vitamin",      False, 1000.0),
    "nia":      ("NIA",        "Niacin (B3)",            "mg",   "vitamin",      False, 1000.0),
    "pantac":   ("PANTAC",     "Pantothenic acid (B5)",  "mg",   "vitamin",      False, 1000.0),
    "vitb6c":   ("VITB6A",     "Vitamin B6",             "mg",   "vitamin",      False, 1000.0),
    "biot":     ("BIOT",       "Biotin (B7)",            "ug",   "vitamin",      False, 1_000_000.0),
    "folsum":   ("FOLSUM",     "Total folates (B9)",     "ug",   "vitamin",      True,  1_000_000.0),
    "vitc":     ("VITC",       "Vitamin C",              "mg",   "vitamin",      True,  1000.0),
    "vita":     ("VITA",       "Vitamin A",              "ug",   "vitamin",      True,  1_000_000.0),
    "vitd":     ("VITD",       "Vitamin D",              "ug",   "vitamin",      False, 1_000_000.0),
    "vite":     ("VITE",       "Vitamin E (a-TE)",       "mg",   "vitamin",      False, 1000.0),
    "vitk":     ("VITK",       "Vitamin K",              "ug",   "vitamin",      False, 1_000_000.0),

    # ---- bioactives relevant to glycemic and mineral bioavailability ------
    "phytac":   ("PHYTAC",     "Phytate",                "mg",   "bioactive",    False, 1000.0),
    "polyph":   ("POLYPH",     "Total polyphenols",      "mg",   "bioactive",    False, 1000.0),
    "oxalt":    ("OXALT",      "Total oxalate",          "mg",   "bioactive",    False, 1000.0),
}

# Nutrients where a reported 0 is a physical fact rather than "not measured".
ZERO_IS_REAL = {
    "FAT", "CHOAVLDF", "FIBTG", "PROCNT", "CHOLE",
    "FASAT", "FAMS", "FAPU", "FATRN", "SUGAR", "STARCH",
}

# IFCT prefixes in the "Local Name" column -> ref.food_name.lang.
# IFCT lists many more languages; these are the four the product resolves against.
LANG_PREFIXES = {
    "Tam.": "ta",
    "Tel.": "te",
    "Mal.": "ml",
    "Kan.": "kn",
}

# IFCT food-group label -> the internal food_group vocabulary used for retention
# and yield lookup. Anything unmapped keeps the IFCT label lowercased.
GROUP_MAP = {
    "Cereals and Millets": "cereal",
    "Grain Legumes": "pulse",
    "Green Leafy Vegetables": "leafy_vegetable",
    "Other Vegetables": "vegetable",
    "Roots and Tubers": "root_tuber",
    "Nuts and Oil Seeds": "nut_oilseed",
    "Condiments and Spices": "spice",
    "Fruits": "fruit",
    "Sugars": "sugar",
    "Milk and Milk Products": "dairy",
    "Edible Oils and Fats": "fat",
    "Fish and Shellfish": "seafood",
    "Meat and Poultry": "meat",
    "Egg and Egg Products": "egg",
    "Miscellaneous Foods": "miscellaneous",
}


@dataclass(frozen=True)
class IFCTFood:
    code: str                        # 'A014'
    name: str                        # 'Rice, parboiled, milled'
    scientific_name: str
    food_group: str                  # mapped internal group
    ifct_group_label: str            # the source's own label
    n_regions: int
    local_names: dict[str, str]      # {'ta': 'Puzhungal arisi', ...}
    values: dict[str, tuple[float, float | None]]   # tagname -> (value, sd)
    derived: frozenset[str] = frozenset()   # tagnames calculated, not measured


def _short_keys(fieldnames: list[str]) -> dict[str, str]:
    """The CSV header is 'Label; code'; index by the code half."""
    return {name.split(";")[-1].strip(): name for name in fieldnames}


def _num(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_local_names(raw: str) -> dict[str, str]:
    """'... Tam. Keerai vidai; Tel. Thotakoora ginjalu.' -> {'ta': ..., 'te': ...}"""
    out: dict[str, str] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip().rstrip(".").strip()
        if not chunk:
            continue
        for prefix, lang in LANG_PREFIXES.items():
            if chunk.startswith(prefix):
                name = chunk[len(prefix):].strip()
                if name:
                    out[lang] = name
                break
    return out


def read_ifct_csv(path: str | Path) -> Iterator[IFCTFood]:
    """Yield one IFCTFood per row of the compositions table."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"{path}: no header row")
        keys = _short_keys(list(reader.fieldnames))

        required = {"code", "name", "grup"}
        missing = required - set(keys)
        if missing:
            raise ValueError(f"{path}: missing expected columns {sorted(missing)}")

        for row in reader:
            code = (row[keys["code"]] or "").strip()
            name = (row[keys["name"]] or "").strip()
            if not code or not name:
                continue

            values: dict[str, tuple[float, float | None]] = {}
            for ifct_code, (tag, _n, _u, _g, _c, factor) in NUTRIENT_MAP.items():
                col = keys.get(ifct_code)
                if col is None:
                    continue
                value = _num(row[col])
                if value is None:
                    continue
                if value == 0.0 and tag not in ZERO_IS_REAL:
                    continue          # "not reported", not a measured zero

                sd_col = keys.get(f"{ifct_code}_e")
                sd = _num(row[sd_col]) if sd_col else None
                values[tag] = (
                    value * factor,
                    None if sd is None else abs(sd) * factor,
                )

            # IFCT reports no energy for the pure fats and oils (group T): the
            # column is 0, which this reader treats as "not reported". Energy for a
            # food whose macronutrients ARE reported is not a guess -- it is the
            # Atwater sum -- so derive it and mark it as calculated rather than
            # leaving the dish it goes into silently short of its energy.
            derived: set[str] = set()
            if "ENERC_KCAL" not in values and any(
                t in values for t in ("PROCNT", "FAT", "CHOAVLDF")
            ):
                kcal = (
                    4.0 * values.get("PROCNT", (0.0, None))[0]
                    + 9.0 * values.get("FAT", (0.0, None))[0]
                    + 4.0 * values.get("CHOAVLDF", (0.0, None))[0]
                    + 2.0 * values.get("FIBTG", (0.0, None))[0]
                )
                if kcal > 0:
                    values["ENERC_KCAL"] = (kcal, None)
                    derived.add("ENERC_KCAL")

            group_label = (row[keys["grup"]] or "").strip()
            yield IFCTFood(
                code=code,
                name=name,
                scientific_name=(row[keys["scie"]] or "").strip() if "scie" in keys else "",
                food_group=GROUP_MAP.get(group_label, group_label.lower() or "miscellaneous"),
                ifct_group_label=group_label,
                n_regions=int(_num(row[keys["regn"]]) or 0) if "regn" in keys else 0,
                local_names=_parse_local_names(row[keys["lang"]]) if "lang" in keys else {},
                values=values,
                derived=frozenset(derived),
            )


@dataclass
class IFCTLoadResult:
    nutrients_upserted: int = 0
    foods_created: int = 0
    foods_matched: int = 0
    values_written: int = 0
    names_written: int = 0
    foods_skipped_no_values: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "nutrients_upserted": self.nutrients_upserted,
            "foods_created": self.foods_created,
            "foods_matched": self.foods_matched,
            "values_written": self.values_written,
            "local_names_written": self.names_written,
            "foods_skipped_no_values": self.foods_skipped_no_values,
        }


_SOURCE_KEY = "IFCT2017"
_SOURCE_CITATION = (
    "Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K. "
    "Indian Food Composition Tables 2017. ICMR-NIN, Hyderabad."
)


def _supersede_open_rows(cur, food_id: int, nutrient_id: int, basis: str = "per_100g") -> None:
    """Close out any composition row for this food/nutrient carried from an earlier day.

    ``composition_unique_ck`` is UNIQUE (food_id, nutrient_id, basis, valid_from) and
    ``valid_from`` defaults to ``current_date``, so an ON CONFLICT upsert only matches
    rows written the SAME day. Re-running a loader the next day therefore inserted a
    second live row beside the first instead of updating it, and because the engine
    sums every row with ``valid_to IS NULL`` the nutrient silently doubled -- puttu
    went from 272 to 471 kcal/100 g overnight with no code change.

    The schema is a temporal one and already means for this to be handled by closing
    the old row rather than deleting it, so history stays intact and the read path
    (``valid_to IS NULL``) picks up exactly one value.
    """
    cur.execute(
        """UPDATE ref.composition_value
              SET valid_to = current_date
            WHERE food_id = %s AND nutrient_id = %s AND basis = %s
              AND valid_to IS NULL AND valid_from < current_date""",
        (food_id, nutrient_id, basis),
    )


def _next_pathyam_food_id(cur) -> int:
    cur.execute(
        r"""SELECT coalesce(max((substring(pathyam_id from 6))::int), 0) + 1
              FROM ref.food_item
             WHERE pathyam_id ~ '^PY-F-[0-9]{6}$'"""
    )
    return int(cur.fetchone()[0])


def load_ifct_into_postgres(
    csv_path: str | Path,
    conn,
    *,
    dry_run: bool = False,
    confidence: str = "A",
) -> IFCTLoadResult:
    """Upsert nutrients, foods, composition values and local names from IFCT 2017.

    The source row is written with ``is_commercial_cleared = false`` and is never
    flipped to true here: ICMR-NIN commercial reuse permission is not established,
    and ``ref.v_uncleared_values`` is the release gate that depends on it.
    """
    result = IFCTLoadResult()

    with conn.cursor() as cur:
        # ---- source (deliberately uncleared) ------------------------------
        cur.execute(
            """INSERT INTO ref.source
                   (source_key, citation, doi_or_url, licence, is_commercial_cleared, notes)
               VALUES (%s, %s, %s, %s, false, %s)
               ON CONFLICT (source_key) DO UPDATE
                   SET citation = EXCLUDED.citation,
                       doi_or_url = EXCLUDED.doi_or_url,
                       notes = EXCLUDED.notes
               RETURNING source_id""",
            (
                _SOURCE_KEY,
                _SOURCE_CITATION,
                "https://www.nin.res.in/ebooks/IFCT2017.pdf",
                "Not stated - ICMR copyright",
                "AWAITING WRITTEN PERMISSION. Ingested from @ifct2017/compositions "
                "(MIT packaging over ICMR-NIN data). Deliberately left uncleared so "
                "it shows in ref.v_uncleared_values.",
            ),
        )
        source_id = cur.fetchone()[0]

        # ---- nutrients ----------------------------------------------------
        nutrient_ids: dict[str, int] = {}
        for order, (tag, name, unit, group, is_core, _f) in enumerate(
            (v for v in NUTRIENT_MAP.values()), start=1
        ):
            cur.execute(
                """INSERT INTO ref.nutrient
                       (infoods_tagname, name, unit, nutrient_group, is_core, display_order)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (infoods_tagname) DO UPDATE
                       SET name = EXCLUDED.name,
                           unit = EXCLUDED.unit,
                           nutrient_group = EXCLUDED.nutrient_group,
                           is_core = ref.nutrient.is_core OR EXCLUDED.is_core
                   RETURNING nutrient_id""",
                (tag, name, unit, group, is_core, order),
            )
            nutrient_ids[tag] = cur.fetchone()[0]
            result.nutrients_upserted += 1

        next_food = _next_pathyam_food_id(cur)

        for food in read_ifct_csv(csv_path):
            if not food.values:
                result.foods_skipped_no_values += 1
                continue

            # Match on IFCT code first -- it is the stable key. Fall back to the
            # canonical name so foods the template loader already created (which
            # carry a name but may lack a code) are enriched rather than duplicated.
            cur.execute("SELECT food_id FROM ref.food_item WHERE ifct_code = %s", (food.code,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT food_id FROM ref.food_item WHERE lower(canonical_name_en) = lower(%s)",
                    (food.name,),
                )
                row = cur.fetchone()

            if row is not None:
                food_id = row[0]
                cur.execute(
                    """UPDATE ref.food_item
                          SET scientific_name = coalesce(nullif(%s, ''), scientific_name),
                              food_group = %s,
                              ifct_code = %s,
                              updated_at = now()
                        WHERE food_id = %s""",
                    (food.scientific_name, food.food_group, food.code, food_id),
                )
                result.foods_matched += 1
            else:
                pathyam_id = f"PY-F-{next_food:06d}"
                next_food += 1
                cur.execute(
                    """INSERT INTO ref.food_item
                           (pathyam_id, canonical_name_en, scientific_name,
                            food_group, ifct_code, is_recipe)
                       VALUES (%s, %s, nullif(%s, ''), %s, %s, false)
                       RETURNING food_id""",
                    (pathyam_id, food.name, food.scientific_name,
                     food.food_group, food.code),
                )
                food_id = cur.fetchone()[0]
                result.foods_created += 1

            # ---- composition values ---------------------------------------
            for tag, (value, sd) in food.values.items():
                is_derived = tag in food.derived
                # A calculated value is not an analysed one. Tier B keeps it usable
                # while making it visible in ref.v_confidence_profile.
                tier = "B" if is_derived else confidence
                method = (
                    "calculated (Atwater 4/9/4/2 from reported macronutrients)"
                    if is_derived else None
                )
                _supersede_open_rows(cur, food_id, nutrient_ids[tag])
                cur.execute(
                    """INSERT INTO ref.composition_value
                           (food_id, nutrient_id, value, basis, sd, n_samples,
                            confidence, source_id, analytical_method, notes)
                       VALUES (%s, %s, %s, 'per_100g', %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (food_id, nutrient_id, basis, valid_from) DO UPDATE
                           SET value = EXCLUDED.value,
                               sd = EXCLUDED.sd,
                               n_samples = EXCLUDED.n_samples,
                               confidence = EXCLUDED.confidence,
                               source_id = EXCLUDED.source_id,
                               analytical_method = EXCLUDED.analytical_method,
                               notes = EXCLUDED.notes""",
                    (
                        food_id, nutrient_ids[tag], round(value, 5),
                        None if sd is None else round(sd, 5),
                        food.n_regions or None,
                        tier, source_id, method,
                        f"IFCT 2017 {food.code}; composite of {food.n_regions} region(s)"
                        if food.n_regions else f"IFCT 2017 {food.code}",
                    ),
                )
                result.values_written += 1

            # ---- native-language names ------------------------------------
            for lang, native in food.local_names.items():
                cur.execute(
                    """INSERT INTO ref.food_name
                           (food_id, lang, name_roman, region_id, is_primary,
                            is_colloquial, source_id)
                       VALUES (%s, %s, %s, NULL, false, false, %s)
                       ON CONFLICT DO NOTHING""",
                    (food_id, lang, native, source_id),
                )
                result.names_written += cur.rowcount

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result

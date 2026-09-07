"""Composition for foods IFCT 2017 does not carry.

A handful of ingredients real South Indian recipes need are absent from IFCT 2017 --
it is a table of raw and minimally processed foods, so refined sugar, table salt,
rice flour, curd and coconut-milk extracts have no row. Leaving them empty blocks
templates; inventing values for them is what this codebase is being cleaned up from.

So each one here is filled by a stated mechanism, and the mechanism determines the
confidence tier. There are exactly two:

BORROWED -- the composition of a closely related food, reused.
    ``is_borrowed = true`` with ``borrowed_from_food_id`` set. The schema's
    ``composition_borrowed_ck`` constraint refuses tier A or B for these, so a
    borrowed value can never masquerade as an analysed one. Tier C.

DEFINITIONAL -- fixed by chemistry, not by analysis.
    Refined sugar is sucrose; iodised salt is sodium chloride. Their composition
    follows from stoichiometry and is not a measurement of a sample. Tier B, with
    the derivation written into ``analytical_method`` so it can be checked.

NOT FILLED HERE
---------------
Coconut milk, first and second extract. These are preparations, not foods: their
composition depends on the kernel-to-water ratio and extraction, and picking a
ratio would be inventing the number this module exists to avoid. The project's own
documented source fallback (dossier §1) is IFCT 2017 -> IFCT 2004 -> UK CoFID 2021
-> USDA FoodData Central, and USDA FDC carries coconut milk as US Government work in
the public domain. That is the correct next source, not a guess here. Until then the
appam template stays blocked, which is the honest state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["BORROWED_FOODS", "DEFINITIONAL_FOODS", "load_derived_foods", "DerivedLoadResult"]


@dataclass(frozen=True)
class BorrowedFood:
    """A food whose composition is reused wholesale from a related IFCT food."""

    name: str                 # canonical_name_en as authored in ingredients.yaml
    source_ifct_code: str     # the IFCT food to borrow from
    rationale: str
    scale: dict[str, float] = field(default_factory=dict)   # tagname -> multiplier


@dataclass(frozen=True)
class DefinitionalFood:
    """A food whose composition follows from chemistry rather than analysis."""

    name: str
    derivation: str                       # goes into analytical_method, verbatim
    values: dict[str, float]              # tagname -> value in ref.nutrient's unit


BORROWED_FOODS = [
    BorrowedFood(
        name="Rice flour",
        source_ifct_code="A015",          # Rice, raw, milled
        rationale=(
            "Rice flour is milled rice. Milling changes particle size, not "
            "composition, so the parent food's values carry over unchanged."
        ),
    ),
    BorrowedFood(
        name="Curd, cow milk",
        source_ifct_code="L002",          # Milk, whole, Cow
        rationale=(
            "Curd is milk fermented by lactic acid bacteria. Protein, fat, minerals "
            "and water are essentially conserved; the change is lactose converted to "
            "lactic acid, so the borrowed carbohydrate figure reads high and the "
            "B-vitamins are approximate. Tier C, pending a measured Indian dairy source."
        ),
        # Roughly 20-30% of lactose is fermented in set curd. Scaling the borrowed
        # carbohydrate is a correction toward the truth, not a new measurement, and
        # it stays tier C either way.
        scale={"CHOAVLDF": 0.75},
    ),
]


DEFINITIONAL_FOODS = [
    DefinitionalFood(
        name="Sugar, refined",
        derivation=(
            "definitional: refined sugar is sucrose (C12H22O11). 100 g is 100 g "
            "available carbohydrate; energy by Atwater at 4 kcal/g."
        ),
        values={
            "CHOAVLDF": 100.0,
            "ENERC_KCAL": 400.0,
            "PROCNT": 0.0,
            "FAT": 0.0,
            "FIBTG": 0.0,
            "WATER": 0.0,
            "ASH": 0.0,
        },
    ),
    DefinitionalFood(
        name="Salt, iodised",
        derivation=(
            "definitional: sodium chloride. Na is 22.990 of NaCl's 58.443 g/mol, "
            "so 100 g NaCl carries 39.34 g = 39337 mg sodium. Contributes no energy."
        ),
        values={
            "NA": 39337.0,
            "ENERC_KCAL": 0.0,
            "PROCNT": 0.0,
            "FAT": 0.0,
            "CHOAVLDF": 0.0,
            "FIBTG": 0.0,
            "WATER": 0.0,
            # NaCl is entirely mineral residue.
            "ASH": 100.0,
        },
    ),
]


_SOURCE_KEY = "PATHYAM-DERIVED"


@dataclass
class DerivedLoadResult:
    borrowed_values: int = 0
    definitional_values: int = 0
    foods_touched: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "borrowed_values": self.borrowed_values,
            "definitional_values": self.definitional_values,
            "foods_touched": self.foods_touched,
            "skipped": self.skipped,
        }


def _food_id_by_name(cur, name: str) -> int | None:
    cur.execute(
        "SELECT food_id FROM ref.food_item WHERE lower(canonical_name_en) = lower(%s)",
        (name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _food_id_by_code(cur, code: str) -> int | None:
    cur.execute("SELECT food_id FROM ref.food_item WHERE ifct_code = %s", (code,))
    row = cur.fetchone()
    return row[0] if row else None


def load_derived_foods(conn, *, dry_run: bool = False) -> DerivedLoadResult:
    """Fill composition for the foods IFCT 2017 has no row for.

    Idempotent: re-running replaces the values it wrote previously.
    """
    result = DerivedLoadResult()

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ref.source
                   (source_key, citation, licence, is_commercial_cleared, notes)
               VALUES (%s, %s, %s, true, %s)
               ON CONFLICT (source_key) DO UPDATE SET notes = EXCLUDED.notes
               RETURNING source_id""",
            (
                _SOURCE_KEY,
                "Pathyam derived composition: values borrowed from a related IFCT 2017 "
                "food, or fixed by chemical definition.",
                "Proprietary",
                "Every value here is either tier C and flagged is_borrowed with its "
                "parent food recorded, or tier B with its derivation in "
                "analytical_method. No value in this source is an analytical result.",
            ),
        )
        source_id = cur.fetchone()[0]

        cur.execute("SELECT infoods_tagname, nutrient_id FROM ref.nutrient")
        nutrient_ids = dict(cur.fetchall())

        # ---- borrowed ------------------------------------------------------
        for spec in BORROWED_FOODS:
            target = _food_id_by_name(cur, spec.name)
            parent = _food_id_by_code(cur, spec.source_ifct_code)
            if target is None or parent is None:
                result.skipped.append(
                    f"{spec.name}: "
                    + ("target food row absent" if target is None
                       else f"parent {spec.source_ifct_code} absent")
                )
                continue

            cur.execute(
                """SELECT n.infoods_tagname, cv.value, cv.sd
                     FROM ref.composition_value cv
                     JOIN ref.nutrient n USING (nutrient_id)
                    WHERE cv.food_id = %s AND cv.valid_to IS NULL""",
                (parent,),
            )
            parent_values = cur.fetchall()
            if not parent_values:
                result.skipped.append(f"{spec.name}: parent has no composition")
                continue

            for tag, value, sd in parent_values:
                nid = nutrient_ids.get(tag)
                if nid is None:
                    continue
                scaled = float(value) * spec.scale.get(tag, 1.0)
                cur.execute(
                    """INSERT INTO ref.composition_value
                           (food_id, nutrient_id, value, basis, sd, confidence,
                            source_id, analytical_method, is_borrowed,
                            borrowed_from_food_id, notes)
                       VALUES (%s, %s, %s, 'per_100g', %s, 'C', %s, %s, true, %s, %s)
                       ON CONFLICT (food_id, nutrient_id, basis, valid_from) DO UPDATE
                           SET value = EXCLUDED.value,
                               sd = EXCLUDED.sd,
                               confidence = EXCLUDED.confidence,
                               source_id = EXCLUDED.source_id,
                               analytical_method = EXCLUDED.analytical_method,
                               is_borrowed = EXCLUDED.is_borrowed,
                               borrowed_from_food_id = EXCLUDED.borrowed_from_food_id,
                               notes = EXCLUDED.notes""",
                    (
                        target, nid, round(scaled, 5),
                        None if sd is None else round(float(sd) * spec.scale.get(tag, 1.0), 5),
                        source_id,
                        f"borrowed from IFCT {spec.source_ifct_code}"
                        + (f", scaled x{spec.scale[tag]}" if tag in spec.scale else ""),
                        parent, spec.rationale,
                    ),
                )
                result.borrowed_values += 1
            result.foods_touched += 1

        # ---- definitional --------------------------------------------------
        for definitional in DEFINITIONAL_FOODS:
            target = _food_id_by_name(cur, definitional.name)
            if target is None:
                result.skipped.append(f"{definitional.name}: food row absent")
                continue

            for tag, value in definitional.values.items():
                nid = nutrient_ids.get(tag)
                if nid is None:
                    continue
                cur.execute(
                    """INSERT INTO ref.composition_value
                           (food_id, nutrient_id, value, basis, sd, confidence,
                            source_id, analytical_method, is_borrowed, notes)
                       VALUES (%s, %s, %s, 'per_100g', 0, 'B', %s, %s, false, %s)
                       ON CONFLICT (food_id, nutrient_id, basis, valid_from) DO UPDATE
                           SET value = EXCLUDED.value,
                               sd = EXCLUDED.sd,
                               confidence = EXCLUDED.confidence,
                               source_id = EXCLUDED.source_id,
                               analytical_method = EXCLUDED.analytical_method,
                               is_borrowed = false,
                               borrowed_from_food_id = NULL,
                               notes = EXCLUDED.notes""",
                    (target, nid, round(value, 5), source_id,
                     definitional.derivation,
                     "Fixed by chemical definition, not measured from a sample."),
                )
                result.definitional_values += 1
            result.foods_touched += 1

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result

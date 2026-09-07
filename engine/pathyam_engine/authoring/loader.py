"""Load an authored template library into PostgreSQL.

Idempotent: re-running updates in place rather than duplicating. Ingredients are
matched on ``canonical_name_en`` rather than on a generated id, so adding a new
ingredient to the middle of ``ingredients.yaml`` does not renumber everything that
follows and silently repoint existing templates.

Composition values are never written here. Identity only — see the header of
``db/templates/ingredients.yaml`` for why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .schema import TemplateLibrary

__all__ = ["LoadResult", "load_into_postgres"]


@dataclass
class LoadResult:
    foods_created: int = 0
    foods_updated: int = 0
    templates_written: int = 0
    parameters_written: int = 0
    ingredients_written: int = 0
    regional_priors_written: int = 0
    skipped: list[str] = None          # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []

    def as_dict(self) -> dict[str, Any]:
        return {
            "foods_created": self.foods_created, "foods_updated": self.foods_updated,
            "templates_written": self.templates_written,
            "parameters_written": self.parameters_written,
            "ingredients_written": self.ingredients_written,
            "regional_priors_written": self.regional_priors_written,
            "skipped": self.skipped,
        }


def _next_pathyam_id(cur, prefix: str, pattern: str) -> int:
    cur.execute(
        f"""SELECT coalesce(max(substring(pathyam_id from 6)::int), 0)
              FROM {'ref.food_item' if prefix == 'PY-F' else 'ref.recipe_template'}
             WHERE pathyam_id ~ %s""",
        (pattern,),
    )
    return int(cur.fetchone()[0]) + 1


def load_into_postgres(
    library: TemplateLibrary,
    conn,
    *,
    source_key: str = "PATHYAM-AUTHORED",
    dry_run: bool = False,
    dish_names: dict[str, str] | None = None,
) -> LoadResult:
    """Write ingredients, dishes, templates, parameters and regional priors."""
    if not library.ok:
        raise ValueError(
            f"library has {len(library.errors)} validation error(s); fix before loading"
        )

    result = LoadResult()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ref.source (source_key, citation, licence, is_commercial_cleared, notes)
               VALUES (%s, %s, %s, true, %s)
               ON CONFLICT (source_key) DO UPDATE SET notes = EXCLUDED.notes
               RETURNING source_id""",
            (source_key, "Pathyam authored recipe templates.", "Proprietary",
             "Template structure and parameter priors. Composition values come from "
             "licensed sources, never from this loader."),
        )
        source_id = cur.fetchone()[0]

        food_ids: dict[str, int] = {}
        next_food = _next_pathyam_id(cur, "PY-F", r"^PY-F-\d{6}$")

        # ---- ingredients (identity only) ---------------------------------
        for key, ing in sorted(library.ingredients.items()):
            # Match on the IFCT code first. It is the stable join to the composition
            # table loaded by ifct2017.py, and the authored English name deliberately
            # differs from IFCT's ("Black pepper" vs "Pepper, black"), so matching on
            # name alone creates a duplicate food that has no composition attached.
            row = None
            if ing.ifct_code:
                cur.execute(
                    "SELECT food_id FROM ref.food_item WHERE ifct_code = %s",
                    (ing.ifct_code,),
                )
                row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT food_id FROM ref.food_item WHERE canonical_name_en = %s",
                    (ing.en,),
                )
                row = cur.fetchone()
            if row:
                food_ids[key] = row[0]
                cur.execute(
                    """UPDATE ref.food_item
                          SET food_group = %s,
                              ifct_code = coalesce(%s, ifct_code),
                              density_g_per_ml = coalesce(%s, density_g_per_ml),
                              edible_portion_pct = coalesce(%s, edible_portion_pct),
                              foodon_iri = coalesce(%s, foodon_iri),
                              updated_at = now()
                        WHERE food_id = %s""",
                    (ing.group, ing.ifct_code, ing.density, ing.edible_pct,
                     ing.foodon, row[0]),
                )
                result.foods_updated += 1
            else:
                pathyam_id = f"PY-F-{next_food:06d}"
                next_food += 1
                cur.execute(
                    """INSERT INTO ref.food_item
                         (pathyam_id, canonical_name_en, food_group, ifct_code,
                          density_g_per_ml, edible_portion_pct, foodon_iri, is_recipe)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, false)
                       RETURNING food_id""",
                    (pathyam_id, ing.en, ing.group, ing.ifct_code, ing.density,
                     ing.edible_pct, ing.foodon),
                )
                food_ids[key] = cur.fetchone()[0]
                result.foods_created += 1

        # ---- dish rows (is_recipe) ---------------------------------------
        dish_food_ids: dict[str, int] = {}
        names = dish_names or {}
        for tpl in library.templates.values():
            # Prefer the lexicon's canonical English name. Title-casing the key gives
            # "Dosa Plain" where the lexicon says "Dosa, plain", which silently
            # creates a second food_item the resolver will never match.
            name = names.get(tpl.dish) or tpl.dish.replace("_", " ").title()
            cur.execute(
                "SELECT food_id FROM ref.food_item WHERE canonical_name_en = %s",
                (name,),
            )
            row = cur.fetchone()
            if row:
                dish_food_ids[tpl.dish] = row[0]
            else:
                pathyam_id = f"PY-F-{next_food:06d}"
                next_food += 1
                cur.execute(
                    """INSERT INTO ref.food_item
                         (pathyam_id, canonical_name_en, food_group, is_recipe)
                       VALUES (%s, %s, 'prepared_dish', true) RETURNING food_id""",
                    (pathyam_id, name),
                )
                dish_food_ids[tpl.dish] = cur.fetchone()[0]
                result.foods_created += 1

        # ---- templates ---------------------------------------------------
        template_ids: dict[str, int] = {}
        for tid, tpl in sorted(library.templates.items()):
            cur.execute(
                """INSERT INTO ref.recipe_template
                     (pathyam_id, food_id, base_method, default_servings,
                      yield_factor, source_id, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (pathyam_id) DO UPDATE
                     SET food_id = EXCLUDED.food_id,
                         base_method = EXCLUDED.base_method,
                         default_servings = EXCLUDED.default_servings,
                         yield_factor = EXCLUDED.yield_factor,
                         notes = EXCLUDED.notes,
                         version = ref.recipe_template.version + 1,
                         updated_at = now()
                   RETURNING template_id""",
                (tid, dish_food_ids[tpl.dish], tpl.method, tpl.servings,
                 tpl.yield_factor, source_id, tpl.notes),
            )
            template_ids[tid] = cur.fetchone()[0]
            result.templates_written += 1

        # Replace children wholesale: an authored file is the source of truth, and
        # a partial upsert would leave orphaned ingredients from a previous version.
        for tid, template_id in template_ids.items():
            tpl = library.templates[tid]
            cur.execute("DELETE FROM ref.template_ingredient WHERE template_id = %s",
                        (template_id,))
            cur.execute("DELETE FROM ref.template_parameter WHERE template_id = %s",
                        (template_id,))
            cur.execute("DELETE FROM ref.regional_prior WHERE template_id = %s",
                        (template_id,))

            for order, (name, spec) in enumerate(tpl.parameters.items(), start=1):
                cur.execute(
                    """INSERT INTO ref.template_parameter
                         (template_id, param_name, dtype, unit, prior_dist, prior_params,
                          observable_from_image, elicitation_question,
                          elicitation_options, display_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (template_id, name, spec.dtype, spec.unit, spec.dist,
                     json.dumps(spec.params), spec.observable, spec.ask,
                     json.dumps(spec.options) if spec.options else None, order),
                )
                result.parameters_written += 1

            for order, ref in enumerate(tpl.ingredients, start=1):
                cur.execute(
                    """INSERT INTO ref.template_ingredient
                         (template_id, food_id, sub_template_id, qty_expr, unit,
                          preparation_state, cooking_method, is_optional, display_order)
                       VALUES (%s, %s, %s, %s, 'g', %s, %s, %s, %s)""",
                    (template_id,
                     food_ids.get(ref.food) if ref.food else None,
                     template_ids.get(ref.sub_template) if ref.sub_template else None,
                     ref.qty, ref.prep, ref.method or tpl.method, ref.optional, order),
                )
                result.ingredients_written += 1

            for region_key, overrides in tpl.regional.items():
                cur.execute("SELECT region_id FROM ref.region WHERE region_key = %s",
                            (region_key,))
                region_row = cur.fetchone()
                if region_row is None:
                    result.skipped.append(
                        f"{tid}: unknown region_key {region_key!r}, priors not loaded")
                    continue
                for name, spec in overrides.items():
                    cur.execute(
                        """INSERT INTO ref.regional_prior
                             (template_id, region_id, param_name, prior_dist,
                              prior_params, source_id)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (template_id, region_row[0], name, spec.dist,
                         json.dumps(spec.params), source_id),
                    )
                    result.regional_priors_written += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return result

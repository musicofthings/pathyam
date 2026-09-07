"""Data access for the compute engine.

Two implementations behind one interface: :class:`PostgresRepository` for real use,
:class:`InMemoryRepository` so the engine's arithmetic can be unit-tested without a
database. The engine itself never writes SQL.

A note on why the engine recurses rather than using ``ref.expand_template()``:
that SQL function returns a *flattened* view with the chain of quantity expressions,
which is exactly right for inspection and debugging. But a sub-recipe reference
carries a TARGET MASS ("70 g of potato masala"), and converting that into leaf
masses requires knowing the sub-template's own total batch mass - which depends on
its own sampled parameters. That normalisation cannot be expressed as a product of
the expression chain, so the compute path walks the tree itself.
"""

from __future__ import annotations

import abc
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    CompositionValue, Nutrient, RecipeTemplate, RetentionFactor, SourceRef,
    TemplateIngredient, TemplateParameter,
)

__all__ = ["Repository", "InMemoryRepository", "PostgresRepository"]


class Repository(abc.ABC):
    """Everything the engine needs to read."""

    @abc.abstractmethod
    def get_template(self, ref: str | int) -> RecipeTemplate: ...

    @abc.abstractmethod
    def get_nutrients(self) -> list[Nutrient]: ...

    @abc.abstractmethod
    def get_composition(self, food_ids: Sequence[int]) -> dict[int, list[CompositionValue]]: ...

    @abc.abstractmethod
    def get_food_meta(self, food_ids: Sequence[int]) -> dict[int, dict[str, Any]]: ...

    @abc.abstractmethod
    def get_retention_factors(self) -> list[RetentionFactor]: ...

    @abc.abstractmethod
    def get_yield_factor(self, food_group: str, cooking_method: str) -> float | None: ...

    @abc.abstractmethod
    def get_regional_priors(
        self, template_id: int, region_key: str | None
    ) -> dict[str, tuple[str, Mapping[str, Any]]]: ...

    @abc.abstractmethod
    def get_sources(self, source_keys: Iterable[str]) -> list[SourceRef]: ...


# ---------------------------------------------------------------- in-memory ----


class InMemoryRepository(Repository):
    """Plain-Python repository for unit tests and offline experiments."""

    def __init__(
        self,
        templates: Sequence[RecipeTemplate] = (),
        nutrients: Sequence[Nutrient] = (),
        composition: Mapping[int, Sequence[CompositionValue]] | None = None,
        food_meta: Mapping[int, Mapping[str, Any]] | None = None,
        retention: Sequence[RetentionFactor] = (),
        yields: Mapping[tuple[str, str], float] | None = None,
        regional_priors: Mapping[tuple[int, str], Mapping[str, tuple[str, Mapping[str, Any]]]] | None = None,
        sources: Sequence[SourceRef] = (),
    ) -> None:
        self._by_id = {t.template_id: t for t in templates}
        self._by_key = {t.pathyam_id: t for t in templates}
        self._nutrients = list(nutrients)
        self._composition = {k: list(v) for k, v in (composition or {}).items()}
        self._food_meta = {k: dict(v) for k, v in (food_meta or {}).items()}
        self._retention = list(retention)
        self._yields = dict(yields or {})
        self._regional = dict(regional_priors or {})
        self._sources = list(sources)

    def get_template(self, ref: str | int) -> RecipeTemplate:
        template = self._by_id.get(ref) if isinstance(ref, int) else self._by_key.get(ref)
        if template is None:
            raise KeyError(f"template {ref!r} not found")
        return template

    def get_nutrients(self) -> list[Nutrient]:
        return list(self._nutrients)

    def get_composition(self, food_ids: Sequence[int]) -> dict[int, list[CompositionValue]]:
        return {fid: list(self._composition.get(fid, [])) for fid in food_ids}

    def get_food_meta(self, food_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        return {
            fid: self._food_meta.get(fid, {"name": f"food:{fid}", "food_group": "other"})
            for fid in food_ids
        }

    def get_retention_factors(self) -> list[RetentionFactor]:
        return list(self._retention)

    def get_yield_factor(self, food_group: str, cooking_method: str) -> float | None:
        return self._yields.get((food_group, cooking_method))

    def get_regional_priors(
        self, template_id: int, region_key: str | None
    ) -> dict[str, tuple[str, Mapping[str, Any]]]:
        if region_key is None:
            return {}
        return dict(self._regional.get((template_id, region_key), {}))

    def get_sources(self, source_keys: Iterable[str]) -> list[SourceRef]:
        wanted = set(source_keys)
        return [s for s in self._sources if s.source_key in wanted]


# ----------------------------------------------------------------- postgres ----


class PostgresRepository(Repository):
    """Reads the ``ref`` schema over psycopg 3.

    Caches reference data that does not change within a request (nutrients,
    retention factors, templates). Composition lookups are batched by food_id.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._template_cache: dict[Any, RecipeTemplate] = {}
        self._nutrient_cache: list[Nutrient] | None = None
        self._retention_cache: list[RetentionFactor] | None = None

    # -- templates ---------------------------------------------------------

    def get_template(self, ref: str | int) -> RecipeTemplate:
        if ref in self._template_cache:
            return self._template_cache[ref]

        column = "template_id" if isinstance(ref, int) else "pathyam_id"
        with self._conn.cursor() as cur:
            cur.execute(
                f"""SELECT template_id, pathyam_id, food_id, base_method,
                           default_servings, yield_factor, notes
                      FROM ref.recipe_template
                     WHERE {column} = %s AND is_active""",
                (ref,),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"active template {ref!r} not found")
            template_id = row[0]

            cur.execute(
                """SELECT param_name, dtype::text, prior_dist::text, prior_params,
                          unit, observable_from_image, elicitation_question
                     FROM ref.template_parameter
                    WHERE template_id = %s
                    ORDER BY display_order NULLS LAST, param_name""",
                (template_id,),
            )
            parameters = [
                TemplateParameter(
                    param_name=r[0], dtype=r[1], prior_dist=r[2], prior_params=r[3],
                    unit=r[4], observable_from_image=r[5], elicitation_question=r[6],
                )
                for r in cur.fetchall()
            ]

            cur.execute(
                """SELECT qty_expr, food_id, sub_template_id, unit,
                          preparation_state, cooking_method, is_optional
                     FROM ref.template_ingredient
                    WHERE template_id = %s
                    ORDER BY display_order NULLS LAST, template_ingredient_id""",
                (template_id,),
            )
            ingredients = [
                TemplateIngredient(
                    qty_expr=r[0], food_id=r[1], sub_template_id=r[2], unit=r[3],
                    preparation_state=r[4], cooking_method=r[5], is_optional=r[6],
                )
                for r in cur.fetchall()
            ]

        template = RecipeTemplate(
            template_id=template_id, pathyam_id=row[1], food_id=row[2],
            base_method=row[3], default_servings=float(row[4]),
            yield_factor=float(row[5]) if row[5] is not None else None,
            notes=row[6], parameters=parameters, ingredients=ingredients,
        )
        self._template_cache[ref] = template
        self._template_cache[template_id] = template
        return template

    # -- reference data ----------------------------------------------------

    def get_nutrients(self) -> list[Nutrient]:
        if self._nutrient_cache is None:
            with self._conn.cursor() as cur:
                cur.execute(
                    """SELECT nutrient_id, infoods_tagname, name, unit, decimals,
                              nutrient_group, is_core
                         FROM ref.nutrient ORDER BY display_order NULLS LAST, nutrient_id"""
                )
                self._nutrient_cache = [
                    Nutrient(nutrient_id=r[0], tagname=r[1], name=r[2], unit=r[3],
                             decimals=r[4], group=r[5], is_core=r[6])
                    for r in cur.fetchall()
                ]
        return list(self._nutrient_cache)

    def get_composition(self, food_ids: Sequence[int]) -> dict[int, list[CompositionValue]]:
        if not food_ids:
            return {}
        out: dict[int, list[CompositionValue]] = {fid: [] for fid in food_ids}
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT cv.food_id, cv.nutrient_id, cv.value, cv.sd,
                          cv.confidence::text, s.source_key, cv.basis::text, cv.is_borrowed
                     FROM ref.composition_value cv
                     JOIN ref.source s ON s.source_id = cv.source_id
                    WHERE cv.food_id = ANY(%s)
                      AND cv.valid_to IS NULL
                      AND cv.basis = 'per_100g'""",
                (list(food_ids),),
            )
            for r in cur.fetchall():
                out[r[0]].append(
                    CompositionValue(
                        food_id=r[0], nutrient_id=r[1], value=float(r[2]),
                        sd=float(r[3]) if r[3] is not None else None,
                        confidence=r[4], source_key=r[5], basis=r[6], is_borrowed=r[7],
                    )
                )
        return out

    def get_food_meta(self, food_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        if not food_ids:
            return {}
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT food_id, canonical_name_en, food_group, pathyam_id,
                          density_g_per_ml, ifct_code
                     FROM ref.food_item WHERE food_id = ANY(%s)""",
                (list(food_ids),),
            )
            return {
                r[0]: {"name": r[1], "food_group": r[2], "pathyam_id": r[3],
                       "density_g_per_ml": float(r[4]) if r[4] is not None else None,
                       "ifct_code": r[5]}
                for r in cur.fetchall()
            }

    def get_retention_factors(self) -> list[RetentionFactor]:
        if self._retention_cache is None:
            with self._conn.cursor() as cur:
                cur.execute(
                    """SELECT rf.food_group, rf.cooking_method, rf.nutrient_id,
                              rf.pct_retained, s.source_key
                         FROM ref.retention_factor rf
                         JOIN ref.source s ON s.source_id = rf.source_id"""
                )
                self._retention_cache = [
                    RetentionFactor(food_group=r[0], cooking_method=r[1],
                                    nutrient_id=r[2], pct_retained=float(r[3]),
                                    source_key=r[4])
                    for r in cur.fetchall()
                ]
        return list(self._retention_cache)

    def get_yield_factor(self, food_group: str, cooking_method: str) -> float | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT factor FROM ref.yield_factor
                    WHERE food_group = %s AND cooking_method = %s""",
                (food_group, cooking_method),
            )
            row = cur.fetchone()
            return float(row[0]) if row else None

    def get_regional_priors(
        self, template_id: int, region_key: str | None
    ) -> dict[str, tuple[str, Mapping[str, Any]]]:
        if region_key is None:
            return {}
        with self._conn.cursor() as cur:
            # Walk up the region hierarchy: a sub-region inherits its state's priors
            # unless it overrides them. Nearest ancestor wins.
            cur.execute(
                """WITH RECURSIVE chain AS (
                       SELECT region_id, parent_region_id, 0 AS distance
                         FROM ref.region WHERE region_key = %s
                       UNION ALL
                       SELECT r.region_id, r.parent_region_id, c.distance + 1
                         FROM chain c JOIN ref.region r ON r.region_id = c.parent_region_id)
                   SELECT DISTINCT ON (rp.param_name)
                          rp.param_name, rp.prior_dist::text, rp.prior_params
                     FROM ref.regional_prior rp
                     JOIN chain c ON c.region_id = rp.region_id
                    WHERE rp.template_id = %s
                    ORDER BY rp.param_name, c.distance""",
                (region_key, template_id),
            )
            return {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    def get_sources(self, source_keys: Iterable[str]) -> list[SourceRef]:
        keys = list(set(source_keys))
        if not keys:
            return []
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT source_key, citation, licence, is_commercial_cleared
                     FROM ref.source WHERE source_key = ANY(%s) ORDER BY source_key""",
                (keys,),
            )
            return [SourceRef(source_key=r[0], citation=r[1], licence=r[2],
                              is_commercial_cleared=r[3]) for r in cur.fetchall()]

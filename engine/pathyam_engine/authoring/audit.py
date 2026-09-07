"""Audit an authored template library against available composition data.

The output is deliberately shaped as a **worklist**, not a score. The question an
author actually needs answered is "what is stopping these templates from computing",
and the answer is almost always a specific list of ingredients with no IFCT values
loaded yet. That list is the extraction brief for the ICMR-NIN licence conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..engine import ComputeEngine
from .schema import TemplateLibrary

__all__ = ["IngredientGap", "TemplateStatus", "AuditReport", "audit"]

# Plausible cooked energy density per food group, kcal/100 g. Wide on purpose: this
# catches order-of-magnitude authoring slips (a rasam at 200 kcal/100 g), not
# borderline cases. Tightening these before real composition data lands would just
# generate noise.
_ENERGY_BANDS: dict[str, tuple[float, float]] = {
    "steamed": (60, 300),
    "griddled": (120, 400),
    "deep_fried": (200, 550),
    "simmered": (20, 250),
    "assembled": (60, 450),
    "ground": (50, 400),
    "boiled": (40, 250),
}


@dataclass
class IngredientGap:
    key: str
    name: str
    group: str
    ifct_code: str | None
    blocks: list[str] = field(default_factory=list)

    @property
    def blocked_count(self) -> int:
        return len(self.blocks)


@dataclass
class TemplateStatus:
    template_id: str
    dish: str
    computable: bool
    reason: str | None = None
    energy_per_serving: float | None = None
    energy_per_100g: float | None = None
    dominant_param: str | None = None
    interval_width: float | None = None
    qc_failures: list[str] = field(default_factory=list)
    missing_ingredients: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    statuses: list[TemplateStatus]
    gaps: list[IngredientGap]
    library_errors: int = 0
    library_warnings: int = 0

    @property
    def computable(self) -> list[TemplateStatus]:
        return [s for s in self.statuses if s.computable]

    @property
    def blocked(self) -> list[TemplateStatus]:
        return [s for s in self.statuses if not s.computable]

    @property
    def coverage(self) -> float:
        return len(self.computable) / len(self.statuses) if self.statuses else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "templates": len(self.statuses),
            "computable": len(self.computable),
            "blocked": len(self.blocked),
            "coverage": round(self.coverage, 4),
            "ingredient_gaps": len(self.gaps),
            "library_errors": self.library_errors,
            "library_warnings": self.library_warnings,
            "worklist": [
                {"key": g.key, "name": g.name, "ifct_code": g.ifct_code,
                 "blocks": g.blocked_count}
                for g in self.gaps
            ],
        }


def audit(
    library: TemplateLibrary,
    repository,
    *,
    n_samples: int = 800,
) -> AuditReport:
    """Try to compute every template; report what works and what is blocking the rest."""
    engine = ComputeEngine(repository)

    # Which authored ingredients exist in the database with usable composition?
    have_composition: set[str] = set()

    # Match on IFCT code first. The authored English name is deliberately not the
    # IFCT name ("Chilli, green" vs "Chillies, green - all varieties"), so matching
    # on name alone reports ingredients as missing composition that in fact have it.
    code_to_key = {
        ing.ifct_code: key
        for key, ing in library.ingredients.items()
        if ing.ifct_code
    }
    name_to_key = {ing.en: key for key, ing in library.ingredients.items()}
    food_ids: dict[str, int] = {}

    for food_id, meta in _all_foods(repository):
        key = code_to_key.get(meta.get("ifct_code")) or name_to_key.get(meta.get("name", ""))
        if key:
            food_ids[key] = food_id
    if food_ids:
        composition = repository.get_composition(sorted(food_ids.values()))
        for key, food_id in food_ids.items():
            if composition.get(food_id):
                have_composition.add(key)

    gaps: dict[str, IngredientGap] = {}
    statuses: list[TemplateStatus] = []

    for tid, tpl in sorted(library.templates.items()):
        missing = sorted({
            ref.food for ref in tpl.ingredients
            if ref.food and ref.food not in have_composition
        })
        for key in missing:
            ing = library.ingredients.get(key)
            if ing is None:
                continue
            gap = gaps.setdefault(key, IngredientGap(key, ing.en, ing.group, ing.ifct_code))
            gap.blocks.append(tid)

        if missing:
            statuses.append(TemplateStatus(
                template_id=tid, dish=tpl.dish, computable=False,
                reason=f"{len(missing)} ingredient(s) have no composition data",
                missing_ingredients=missing,
            ))
            continue

        try:
            result = engine.compute(tid, n_samples=n_samples)
        except Exception as exc:            # noqa: BLE001 - report, never abort the audit
            statuses.append(TemplateStatus(
                template_id=tid, dish=tpl.dish, computable=False,
                reason=f"{type(exc).__name__}: {exc}",
            ))
            continue

        energy = result.nutrients.get("ENERC_KCAL")
        qc_failures = [q.gate for q in result.qc if q.status == "FAIL"]

        status = TemplateStatus(
            template_id=tid, dish=tpl.dish, computable=True,
            energy_per_serving=energy.per_serving.p50 if energy else None,
            energy_per_100g=energy.per_100g.p50 if energy else None,
            dominant_param=result.dominant_uncertainty_param,
            interval_width=energy.per_serving.relative_width if energy else None,
            qc_failures=qc_failures,
        )

        band = _ENERGY_BANDS.get(tpl.method)
        if band and status.energy_per_100g is not None:
            low, high = band
            if not (low <= status.energy_per_100g <= high):
                status.qc_failures.append(
                    f"energy_band({status.energy_per_100g:.0f} kcal/100g "
                    f"outside {low}-{high} for {tpl.method})"
                )
        statuses.append(status)

    ordered_gaps = sorted(gaps.values(), key=lambda g: (-g.blocked_count, g.key))
    return AuditReport(
        statuses=statuses, gaps=ordered_gaps,
        library_errors=len(library.errors), library_warnings=len(library.warnings),
    )


def _all_foods(repository) -> list[tuple[int, dict[str, Any]]]:
    """Best-effort enumeration of foods, for both repository implementations."""
    conn = getattr(repository, "_conn", None)
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT food_id FROM ref.food_item ORDER BY food_id")
            ids = [r[0] for r in cur.fetchall()]
        return list(repository.get_food_meta(ids).items())

    meta = getattr(repository, "_food_meta", {})
    return list(meta.items())

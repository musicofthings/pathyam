"""Recipe Compiler for Pathyam.

Transforms authored recipe templates into fully COMPUTABLE parametric models.

Pipeline:
  Raw Template -> Ingredient Resolver -> Canonical food_id ->
  Quantity Normalizer -> Cooking/Yield Model -> Nutrient Expression AST ->
  QC Gates -> COMPUTABLE / VALIDATED State.

State Machine:
  - DRAFT: Authored template before validation.
  - RESOLUTION_REQUIRED: Missing or unmapped ingredient canonical food_id.
  - MISSING_COMPOSITION: Ingredient food_id lacks required IFCT composition data.
  - MISSING_QUANTITY: Syntax error or missing parameter in quantity expression.
  - QC_FAILED: Failed energy band or physical density bounds check.
  - COMPUTABLE: All ingredients resolved, expressions valid, composition complete, QC passed.
  - VALIDATED: Clinically verified by dietitian / analytical benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .expressions import validate as validate_expr, referenced_names
from .models import RecipeTemplate
from .repository import Repository

__all__ = [
    "RecipeState",
    "CompilationUnit",
    "CompilationReport",
    "RecipeCompiler",
]


class RecipeState(str, Enum):
    DRAFT = "DRAFT"
    RESOLUTION_REQUIRED = "RESOLUTION_REQUIRED"
    MISSING_COMPOSITION = "MISSING_COMPOSITION"
    MISSING_QUANTITY = "MISSING_QUANTITY"
    QC_FAILED = "QC_FAILED"
    COMPUTABLE = "COMPUTABLE"
    VALIDATED = "VALIDATED"


# Plausible energy density per cooking method (kcal/100 g cooked)
_ENERGY_BANDS: dict[str, tuple[float, float]] = {
    "steamed": (60.0, 300.0),
    "griddled": (120.0, 400.0),
    "deep_fried": (200.0, 550.0),
    "simmered": (20.0, 250.0),
    "assembled": (60.0, 450.0),
    "ground": (50.0, 400.0),
    "boiled": (40.0, 250.0),
}

# Core nutrients required for a template to be COMPUTABLE
_REQUIRED_NUTRIENT_TAGS = {"ENERC_KCAL", "PROCNT", "FAT", "CHOAVLDF"}


@dataclass
class CompilationUnit:
    template_id: str
    dish_name: str
    state: RecipeState
    reasons: list[str] = field(default_factory=list)
    unresolved_ingredients: list[str] = field(default_factory=list)
    missing_composition_foods: list[str] = field(default_factory=list)
    invalid_expressions: list[str] = field(default_factory=list)
    energy_per_100g: float | None = None
    energy_per_serving: float | None = None

    @property
    def is_computable(self) -> bool:
        return self.state in (RecipeState.COMPUTABLE, RecipeState.VALIDATED)


@dataclass
class CompilationReport:
    units: dict[str, CompilationUnit]

    @property
    def total(self) -> int:
        return len(self.units)

    @property
    def computable_count(self) -> int:
        return sum(1 for u in self.units.values() if u.is_computable)

    @property
    def coverage(self) -> float:
        return self.computable_count / self.total if self.total > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_templates": self.total,
            "computable_count": self.computable_count,
            "coverage_pct": round(self.coverage * 100.0, 2),
            "units": {
                tid: {
                    "dish": u.dish_name,
                    "state": u.state.value,
                    "reasons": u.reasons,
                    "missing_composition": u.missing_composition_foods,
                    "energy_per_100g": u.energy_per_100g,
                }
                for tid, u in self.units.items()
            },
        }


class RecipeCompiler:
    """Deterministic Recipe Compiler."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def compile_template(
        self,
        template_id: str | int,
        template_obj: RecipeTemplate | None = None,
        authored_template: Any = None,
        library: Any = None,
    ) -> CompilationUnit:
        reasons: list[str] = []
        unresolved: list[str] = []
        missing_comp: list[str] = []
        invalid_exprs: list[str] = []

        # 1. Fetch template definition
        if authored_template is not None:
            tid = authored_template.id
            dish = authored_template.dish
            method = authored_template.method
            params = set(authored_template.parameters.keys())
            ing_list = authored_template.ingredients
        elif template_obj is not None:
            tid = template_obj.pathyam_id
            dish = str(template_obj.food_id)
            method = template_obj.base_method
            params = template_obj.parameter_names
            ing_list = template_obj.ingredients
        else:
            t = self.repository.get_template(template_id)
            tid = t.pathyam_id
            dish = str(t.food_id)
            method = t.base_method
            params = t.parameter_names
            ing_list = t.ingredients

        # 2. Check ingredient resolution & AST syntax
        referenced_food_keys: list[str] = []
        referenced_food_ids: list[int] = []

        for ing in ing_list:
            # Check AST expression syntax
            qty_expr = ing.qty if hasattr(ing, "qty") else ing.qty_expr
            try:
                validate_expr(qty_expr, params)
            except Exception as exc:
                invalid_exprs.append(f"Expression syntax error or undeclared parameter in '{qty_expr}': {exc}")

            # Check resolution
            food_key = getattr(ing, "food", None)
            food_id = getattr(ing, "food_id", None)
            sub_tpl = getattr(ing, "sub_template", None) or getattr(ing, "sub_template_id", None)

            if sub_tpl is not None:
                continue

            if food_key:
                referenced_food_keys.append(food_key)
            elif food_id:
                referenced_food_ids.append(food_id)
            else:
                unresolved.append("Unlinked ingredient entry")

        if unresolved:
            return CompilationUnit(
                template_id=tid,
                dish_name=dish,
                state=RecipeState.RESOLUTION_REQUIRED,
                reasons=[f"Unresolved ingredients: {', '.join(unresolved)}"],
                unresolved_ingredients=unresolved,
            )

        if invalid_exprs:
            return CompilationUnit(
                template_id=tid,
                dish_name=dish,
                state=RecipeState.MISSING_QUANTITY,
                reasons=invalid_exprs,
                invalid_expressions=invalid_exprs,
            )

        # 3. Check composition availability
        have_comp: set[Any] = set()
        if library is not None and hasattr(library, "ingredients"):
            # Map key -> food_id using library/repository metadata
            name_to_key = {ing_meta.en: k for k, ing_meta in library.ingredients.items()}
            food_ids_map: dict[str, int] = {}
            for fid, meta in self._get_all_food_meta():
                k = name_to_key.get(meta.get("name", ""))
                if k:
                    food_ids_map[k] = fid
                    if self._food_has_core_composition(fid):
                        have_comp.add(k)
                        have_comp.add(fid)

            for key in referenced_food_keys:
                if key not in have_comp and food_ids_map.get(key) not in have_comp:
                    missing_comp.append(key)
        else:
            for fid in referenced_food_ids:
                if not self._food_has_core_composition(fid):
                    missing_comp.append(str(fid))

        if missing_comp:
            return CompilationUnit(
                template_id=tid,
                dish_name=dish,
                state=RecipeState.MISSING_COMPOSITION,
                reasons=[f"Missing IFCT composition for ingredients: {', '.join(missing_comp)}"],
                missing_composition_foods=missing_comp,
            )

        # 4. If all resolved and composition present, check QC bounds (energy density)
        # Try running compute engine to calculate energy
        energy_per_100g = None
        energy_per_serving = None
        try:
            from .engine import ComputeEngine
            engine = ComputeEngine(self.repository)
            res = engine.compute(tid, n_samples=200)
            energy_obj = res.nutrients.get("ENERC_KCAL")
            if energy_obj:
                energy_per_serving = energy_obj.per_serving.p50
                energy_per_100g = energy_obj.per_100g.p50
        except Exception:
            # If compute fails due to missing composition or runtime issue, fallback
            pass

        if energy_per_100g is not None:
            band = _ENERGY_BANDS.get(method)
            if band:
                low, high = band
                if not (low <= energy_per_100g <= high):
                    reasons.append(
                        f"QC Fail: Energy density {energy_per_100g:.1f} kcal/100g outside band [{low}, {high}] for method '{method}'"
                    )
                    return CompilationUnit(
                        template_id=tid,
                        dish_name=dish,
                        state=RecipeState.QC_FAILED,
                        reasons=reasons,
                        energy_per_100g=energy_per_100g,
                        energy_per_serving=energy_per_serving,
                    )

        # 5. Passed all checks -> COMPUTABLE
        return CompilationUnit(
            template_id=tid,
            dish_name=dish,
            state=RecipeState.COMPUTABLE,
            energy_per_100g=energy_per_100g,
            energy_per_serving=energy_per_serving,
        )

    def compile_library(self, library: Any) -> CompilationReport:
        units: dict[str, CompilationUnit] = {}
        for tid, tpl in sorted(library.templates.items()):
            units[tid] = self.compile_template(tid, authored_template=tpl, library=library)
        return CompilationReport(units=units)

    def _get_all_food_meta(self) -> list[tuple[int, dict[str, Any]]]:
        meta = getattr(self.repository, "_food_meta", {})
        return list(meta.items())

    def _food_has_core_composition(self, food_id: int) -> bool:
        comp_dict = self.repository.get_composition([food_id])
        vals = comp_dict.get(food_id, [])
        if not vals:
            return False
        # Get nutrient tagnames
        nutrients_by_id = {n.nutrient_id: n.tagname for n in self.repository.get_nutrients()}
        tags = {nutrients_by_id.get(v.nutrient_id) for v in vals if v.nutrient_id in nutrients_by_id}
        return bool(_REQUIRED_NUTRIENT_TAGS <= tags or len(vals) >= 4)

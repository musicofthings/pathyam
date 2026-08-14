"""Parsing and validation of authored template YAML.

Templates are written by nutritionists, not engineers, and ~400 of them are needed.
At that volume, review cannot be the safety net — validation has to be. Every check
here corresponds to a mistake that would otherwise produce a template that computes
happily and returns a wrong number.

The subtlest one is :func:`_check_categorical_coverage`. If ``fat_type`` offers four
categories but only three ingredient rows select on it, then some fraction of Monte
Carlo samples contain **no fat at all** — and nothing raises, the energy estimate is
just quietly low for that share of draws. Adding a category to a prior and forgetting
the matching ingredient row is a natural editing slip, so it is checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..distributions import Prior, PriorError, sample_prior
from ..expressions import ExpressionError, referenced_names, validate

__all__ = ["Ingredient", "ParamSpec", "IngredientRef", "Template", "TemplateLibrary",
           "ValidationIssue", "load_library"]

_TEMPLATE_ID = re.compile(r"^PY-T-\d{6}$")
_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SELECTOR = re.compile(r'(\w+)\s*==\s*["\']([^"\']+)["\']')

# Plausible bounds. Steamed and simmered dishes gain mass; griddled and fried lose it.
_YIELD_MIN, _YIELD_MAX = 0.20, 5.00


@dataclass(frozen=True)
class ValidationIssue:
    severity: str            # 'error' | 'warning'
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper():7}] {self.where}: {self.message}"


@dataclass(frozen=True)
class Ingredient:
    key: str
    en: str
    group: str
    ifct_code: str | None = None
    density: float | None = None
    edible_pct: float | None = None
    foodon: str | None = None


@dataclass(frozen=True)
class ParamSpec:
    name: str
    dist: str
    params: dict[str, Any]
    unit: str | None = None
    dtype: str = "continuous"
    observable: bool = False
    ask: str | None = None
    options: list[dict[str, Any]] = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        return list(self.params.get("categories", ())) if self.dist == "categorical" else []


@dataclass(frozen=True)
class IngredientRef:
    qty: str
    food: str | None = None
    sub_template: str | None = None
    prep: str | None = None
    method: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class Template:
    id: str
    dish: str
    method: str
    servings: float
    yield_factor: float | None
    parameters: dict[str, ParamSpec]
    ingredients: list[IngredientRef]
    regional: dict[str, dict[str, ParamSpec]] = field(default_factory=dict)
    notes: str | None = None
    source_file: str = ""


@dataclass
class TemplateLibrary:
    ingredients: dict[str, Ingredient]
    templates: dict[str, Template]
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def food_keys_used(self) -> set[str]:
        return {
            ref.food for t in self.templates.values()
            for ref in t.ingredients if ref.food
        }


# --------------------------------------------------------------------- load ----


def _parse_param(name: str, raw: dict[str, Any]) -> ParamSpec:
    dist = raw.get("dist", "point")
    known = {"dist", "unit", "observable", "ask", "options", "dtype"}
    params = {k: v for k, v in raw.items() if k not in known}
    dtype = raw.get("dtype") or ("categorical" if dist == "categorical" else "continuous")
    return ParamSpec(
        name=name, dist=dist, params=params, unit=raw.get("unit"), dtype=dtype,
        observable=bool(raw.get("observable", False)), ask=raw.get("ask"),
        options=list(raw.get("options", ())),
    )


def load_library(
    ingredients_path: str | Path,
    template_paths: Iterable[str | Path],
) -> TemplateLibrary:
    """Load and fully validate an authored library. Never raises on bad content."""
    import yaml

    issues: list[ValidationIssue] = []

    with open(ingredients_path, encoding="utf-8") as fh:
        raw_ingredients = yaml.safe_load(fh)["ingredients"]
    ingredients: dict[str, Ingredient] = {}
    for row in raw_ingredients:
        key = row["key"]
        if key in ingredients:
            issues.append(ValidationIssue("error", key, "duplicate ingredient key"))
        ingredients[key] = Ingredient(
            key=key, en=row["en"], group=row["group"], ifct_code=row.get("ifct_code"),
            density=row.get("density"), edible_pct=row.get("edible_pct"),
            foodon=row.get("foodon"),
        )

    templates: dict[str, Template] = {}
    for path in template_paths:
        try:
            with open(path, encoding="utf-8") as fh:
                payload = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            # Authors edit these files by hand, and the commonest failure is an
            # unquoted value containing ": " — YAML reads it as a nested mapping.
            # A traceback here would send someone hunting a Python bug.
            mark = getattr(exc, "problem_mark", None)
            where = f"{Path(path).name}"
            if mark is not None:
                where += f" line {mark.line + 1}, column {mark.column + 1}"
            issues.append(ValidationIssue(
                "error", where,
                f"YAML parse error: {getattr(exc, 'problem', exc)}. "
                "Check for an unquoted value containing a colon."))
            continue
        for raw in payload.get("templates", ()):
            tid = raw.get("id", "<missing id>")
            if tid in templates:
                issues.append(ValidationIssue("error", tid, "duplicate template id"))
            templates[tid] = Template(
                id=tid, dish=raw.get("dish", ""), method=raw.get("method", ""),
                servings=float(raw.get("servings", 1)),
                yield_factor=(float(raw["yield_factor"])
                              if raw.get("yield_factor") is not None else None),
                parameters={n: _parse_param(n, p)
                            for n, p in (raw.get("parameters") or {}).items()},
                ingredients=[
                    IngredientRef(
                        qty=str(r.get("qty", "")), food=r.get("food"),
                        sub_template=r.get("sub_template"), prep=r.get("prep"),
                        method=r.get("method"), optional=bool(r.get("optional", False)),
                    )
                    for r in (raw.get("ingredients") or ())
                ],
                regional={
                    region: {n: _parse_param(n, p) for n, p in overrides.items()}
                    for region, overrides in (raw.get("regional") or {}).items()
                },
                notes=raw.get("notes"), source_file=str(path),
            )

    library = TemplateLibrary(ingredients=ingredients, templates=templates, issues=issues)
    _validate(library)
    return library


# ----------------------------------------------------------------- validate ----


def _validate(lib: TemplateLibrary) -> None:
    add = lib.issues.append

    for tid, tpl in lib.templates.items():
        if not _TEMPLATE_ID.match(tid):
            add(ValidationIssue("error", tid, "id must match PY-T-nnnnnn"))
        if tpl.servings <= 0:
            add(ValidationIssue("error", tid, f"servings must be positive, got {tpl.servings}"))
        if tpl.yield_factor is not None and not (_YIELD_MIN <= tpl.yield_factor <= _YIELD_MAX):
            add(ValidationIssue("error", tid,
                                f"yield_factor {tpl.yield_factor} outside "
                                f"[{_YIELD_MIN}, {_YIELD_MAX}] — implausible"))
        if not tpl.ingredients:
            add(ValidationIssue("error", tid, "template has no ingredients"))
        if not tpl.dish:
            add(ValidationIssue("error", tid, "template has no dish key"))

        _check_parameters(lib, tpl, add)
        _check_ingredients(lib, tpl, add)
        _check_categorical_coverage(lib, tpl, add)
        _check_regional(lib, tpl, add)

    _check_cycles(lib, add)


def _check_parameters(lib, tpl: Template, add) -> None:
    for name, spec in tpl.parameters.items():
        if not _PARAM_NAME.match(name):
            add(ValidationIssue("error", f"{tpl.id}.{name}",
                                "parameter name must match ^[a-z][a-z0-9_]{0,39}$ — "
                                "it is substituted into quantity expressions"))
        # Sample once at load time. A malformed prior that only fails at request time
        # is a production incident; here it is a validation error.
        try:
            import numpy as np
            prior = Prior.from_row(spec.dist, spec.params, unit=spec.unit, dtype=spec.dtype)
            sample_prior(prior, 32, np.random.default_rng(0))
            if prior.warnings:
                add(ValidationIssue("warning", f"{tpl.id}.{name}", prior.warnings[0]))
        except PriorError as exc:
            add(ValidationIssue("error", f"{tpl.id}.{name}", str(exc)))

        if spec.ask and not spec.options:
            add(ValidationIssue("warning", f"{tpl.id}.{name}",
                                "has an elicitation question but no options to choose from"))


def _check_ingredients(lib, tpl: Template, add) -> None:
    declared = set(tpl.parameters)
    for index, ref in enumerate(tpl.ingredients):
        where = f"{tpl.id}.ingredients[{index}]"

        if (ref.food is None) == (ref.sub_template is None):
            add(ValidationIssue("error", where,
                                "must reference exactly one of food or sub_template"))
            continue
        if ref.food and ref.food not in lib.ingredients:
            add(ValidationIssue("error", where,
                                f"unknown ingredient key {ref.food!r} — "
                                "add it to ingredients.yaml"))
        if ref.sub_template and ref.sub_template not in lib.templates:
            add(ValidationIssue("error", where,
                                f"unknown sub_template {ref.sub_template!r}"))
        try:
            validate(ref.qty, declared)
        except ExpressionError as exc:
            add(ValidationIssue("error", where, str(exc)))


def _check_categorical_coverage(lib, tpl: Template, add) -> None:
    """Every category of a categorical parameter should be selected by some ingredient.

    Miss one and that share of Monte Carlo draws silently contains none of whatever
    the parameter switches — usually cooking fat, usually understating energy.
    """
    selected: dict[str, set[str]] = {}
    for ref in tpl.ingredients:
        for param, value in _SELECTOR.findall(ref.qty):
            selected.setdefault(param, set()).add(value)

    for name, spec in tpl.parameters.items():
        if spec.dist != "categorical":
            continue
        categories = set(spec.categories)
        if not categories:
            continue
        used = selected.get(name, set())
        if not used:
            # Legitimate when the parameter drives something other than ingredient
            # selection (fermentation state, rice type feeding a different branch).
            add(ValidationIssue("warning", f"{tpl.id}.{name}",
                                "categorical parameter is never used as an ingredient "
                                "selector — confirm it affects the computation"))
            continue
        missing = categories - used
        if missing:
            weights = dict(zip(spec.categories, spec.params.get("weights", [])))
            share = sum(float(weights.get(m, 0)) for m in missing)
            add(ValidationIssue(
                "error", f"{tpl.id}.{name}",
                f"categories {sorted(missing)} carry {share:.0%} of the prior weight "
                f"but no ingredient selects them — that share of samples would silently "
                f"contain none of this component"))
        unknown = used - categories
        if unknown:
            add(ValidationIssue("error", f"{tpl.id}.{name}",
                                f"ingredients select {sorted(unknown)}, which are not "
                                f"categories of this parameter"))


def _check_regional(lib, tpl: Template, add) -> None:
    for region, overrides in tpl.regional.items():
        for name, spec in overrides.items():
            if name not in tpl.parameters:
                add(ValidationIssue("error", f"{tpl.id}.regional.{region}.{name}",
                                    "overrides a parameter the template does not declare"))
                continue
            base = tpl.parameters[name]
            if base.dist == "categorical" and spec.dist == "categorical":
                if set(spec.categories) != set(base.categories):
                    add(ValidationIssue(
                        "error", f"{tpl.id}.regional.{region}.{name}",
                        f"category set differs from the base prior "
                        f"({sorted(spec.categories)} vs {sorted(base.categories)}) — "
                        "ingredient selectors would not cover it"))


def _check_cycles(lib, add) -> None:
    for tid in lib.templates:
        seen: set[str] = set()
        stack = [tid]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            tpl = lib.templates.get(current)
            if tpl is None:
                continue
            for ref in tpl.ingredients:
                if ref.sub_template:
                    if ref.sub_template == tid and current != tid:
                        add(ValidationIssue("error", tid,
                                            f"sub-template cycle via {current}"))
                    stack.append(ref.sub_template)

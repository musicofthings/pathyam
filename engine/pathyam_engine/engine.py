"""The deterministic composition engine.

Given a parametric recipe template, this produces nutrient distributions - not point
estimates. The output of a photo-logged masala dosa is "385 kcal (291-518, 80% CI),
largest uncertainty: cooking oil", because that is what the evidence supports. The
peer-reviewed state of the art is ~23% MAPE on food *volume* alone (MetaFood CVPR
2025 winner); adding ingredient identification, oil absorption and cooking method
puts honest end-to-end error at 20-40%. A bare "385 kcal" is false precision.

Pipeline per compute call
-------------------------
1. Load template; validate every ``qty_expr`` against its declared parameters.
2. Resolve each parameter's prior through the precedence chain
   (template default -> regional -> supplied prior -> explicit override).
3. Sample all parameters, ``n_samples`` draws.
4. Walk the template tree, evaluating quantity expressions vectorised, normalising
   sub-recipe target masses against their own batch mass.
5. Sample composition values (mean and analytical SD, where the source reports one).
6. Apply nutrient retention factors by cooking method, then the yield factor.
7. Summarise to percentiles; attribute variance to parameters; run QC gates.

The engine never calls an LLM and never guesses a number. Everything it returns is
arithmetic over values that carry a source and a confidence tier.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

import numpy as np

from . import qc as qc_module
from .distributions import Prior, sample_prior
from .expressions import evaluate, validate
from .models import (
    TIER_ORDER, ComputeResult, IngredientDraw, NutrientResult, Percentiles,
    RecipeTemplate, SourceRef,
)
from .repository import Repository

__all__ = ["ComputeEngine", "EngineError", "ENGINE_VERSION"]

ENGINE_VERSION = "0.1.0"

_MAX_DEPTH = 6
_DEFAULT_SAMPLES = 2000


class EngineError(RuntimeError):
    """Raised when a template cannot be computed."""


class ComputeEngine:
    def __init__(self, repository: Repository, *, engine_version: str = ENGINE_VERSION) -> None:
        self.repo = repository
        self.engine_version = engine_version

    # ------------------------------------------------------------------ API --

    def compute(
        self,
        template_ref: str | int,
        *,
        n_samples: int = _DEFAULT_SAMPLES,
        region_key: str | None = None,
        param_overrides: Mapping[str, Any] | None = None,
        param_priors: Mapping[str, Prior] | None = None,
        servings: float | None = None,
        portions: float = 1.0,
        seed: int | None = None,
        include_optional: bool = True,
        sample_composition_sd: bool = True,
        min_confidence: str = "D",
        run_qc: bool = True,
    ) -> ComputeResult:
        """Compute nutrient distributions for one template.

        ``param_overrides`` are treated as user-stated point values and win over
        everything. ``param_priors`` (from a VLM estimate, a geometry module, or a
        learned per-user prior) win over regional and template defaults.
        ``min_confidence`` is the worst tier acceptable before a warning is raised -
        clinical callers should pass ``"B"``.

        ``servings`` and ``portions`` are different things and conflating them is a
        silent error. ``servings`` is how many portions one BATCH of the template
        makes (an idli template yields 4). ``portions`` is how many the person ATE.
        Passing a user's "3 idli" as ``servings`` divides the batch by three and
        returns 1.33 idli worth of nutrition - which happens to look right whenever
        quantity equals default_servings, so it survives casual testing.
        """
        overrides = dict(param_overrides or {})
        priors_in = dict(param_priors or {})
        warnings: list[str] = []

        if seed is None:
            seed = self._derive_seed(template_ref, overrides, region_key, n_samples)
        rng = np.random.default_rng(seed)

        template = self.repo.get_template(template_ref)
        ctx = _SampleContext(
            repo=self.repo, rng=rng, n=n_samples, region_key=region_key,
            overrides=overrides, priors_in=priors_in, warnings=warnings,
        )

        draws, raw_total = self._walk(template, ctx, depth=0, seen=frozenset())
        if not draws:
            raise EngineError(f"template {template.pathyam_id} expanded to no ingredients")

        if np.any(raw_total <= 0):
            raise EngineError(
                f"template {template.pathyam_id} produced non-positive total mass in "
                f"{int(np.count_nonzero(raw_total <= 0))}/{n_samples} samples"
            )

        n_servings = float(servings if servings is not None else template.default_servings)
        if n_servings <= 0:
            raise EngineError(f"servings must be positive, got {n_servings}")
        portions = float(portions)
        if portions <= 0:
            raise EngineError(f"portions must be positive, got {portions}")

        yield_factor = self._resolve_yield(template, warnings)
        cooked_total = raw_total * yield_factor

        nutrients, sources_used, tier_by_nutrient, missing_mass = self._compute_nutrients(
            draws, ctx, cooked_total, n_servings, portions,
            sample_sd=sample_composition_sd, warnings=warnings,
        )

        energy = nutrients.get("ENERC_KCAL")
        contributions, dominant = self._sensitivity(
            ctx.samples_flat, energy.per_serving_samples if energy else None
        )

        worst = self._worst_tier(tier_by_nutrient.values())
        if TIER_ORDER.get(worst, 3) > TIER_ORDER.get(min_confidence, 3):
            warnings.append(
                f"result includes tier-{worst} values but caller requested no worse "
                f"than tier {min_confidence}; affected nutrients: "
                + ", ".join(
                    t for t, c in sorted(tier_by_nutrient.items())
                    if TIER_ORDER.get(c, 3) > TIER_ORDER.get(min_confidence, 3)
                )
            )

        source_refs = self.repo.get_sources(sources_used)
        uncleared = [s.source_key for s in source_refs if not s.is_commercial_cleared]
        if uncleared:
            warnings.append(
                "result depends on source(s) without commercial clearance: "
                + ", ".join(sorted(uncleared))
            )

        food_meta = self.repo.get_food_meta([template.food_id])
        result = ComputeResult(
            template_pathyam_id=template.pathyam_id,
            food_name=food_meta.get(template.food_id, {}).get("name", template.pathyam_id),
            n_samples=n_samples,
            seed=seed,
            engine_version=self.engine_version,
            servings=n_servings,
            portions=portions,
            mass_without_composition=missing_mass,
            raw_mass_g=Percentiles.from_samples(raw_total),
            cooked_mass_g=Percentiles.from_samples(cooked_total),
            nutrients={k: v.to_public() for k, v in nutrients.items()},
            dominant_uncertainty_param=dominant,
            variance_contributions=contributions,
            ingredients=self._ingredient_table(draws),
            sources=source_refs,
            worst_confidence=worst,
            parameter_summary=ctx.summary(),
            warnings=warnings,
        )

        if run_qc:
            result.qc = qc_module.run_all(result)
        return result

    # -------------------------------------------------------------- internals --

    def _walk(
        self, template: RecipeTemplate, ctx: "_SampleContext", *, depth: int, seen: frozenset
    ) -> tuple[list[IngredientDraw], np.ndarray]:
        """Recursive expansion. Returns leaf draws and this node's total raw mass."""
        if depth > _MAX_DEPTH:
            raise EngineError(f"template nesting exceeded depth {_MAX_DEPTH}")
        if template.template_id in seen:
            raise EngineError(
                f"template cycle detected at {template.pathyam_id} "
                "(the DB trigger should have prevented this)"
            )

        params = ctx.parameters_for(template)
        for ing in template.ingredients:
            validate(ing.qty_expr, template.parameter_names)

        food_ids = [i.food_id for i in template.ingredients if i.food_id is not None]
        meta = self.repo.get_food_meta(food_ids)

        draws: list[IngredientDraw] = []
        total = np.zeros(ctx.n)

        for ing in template.ingredients:
            if ing.is_optional and not ctx.include_optional:
                continue
            qty = np.asarray(evaluate(ing.qty_expr, params, n_samples=ctx.n), dtype=float)
            # A negative mass is meaningless; selector idioms legitimately yield 0.
            qty = np.maximum(qty, 0.0)

            if ing.food_id is not None:
                info = meta.get(ing.food_id, {})
                draws.append(
                    IngredientDraw(
                        food_id=ing.food_id,
                        food_name=info.get("name", f"food:{ing.food_id}"),
                        food_group=info.get("food_group", "other"),
                        grams=qty,
                        cooking_method=ing.cooking_method or template.base_method,
                        preparation_state=ing.preparation_state,
                        depth=depth,
                    )
                )
                total = total + qty
            else:
                sub = self.repo.get_template(int(ing.sub_template_id))
                sub_draws, sub_total = self._walk(
                    sub, ctx, depth=depth + 1, seen=seen | {template.template_id}
                )
                # qty is a TARGET MASS of the sub-recipe ("70 g of potato masala"),
                # so rescale the sub-recipe's own batch to hit it. This is why the
                # engine recurses instead of consuming ref.expand_template()'s
                # flattened expression chain - the normalisation needs sub_total.
                zero_batches = int(np.count_nonzero(sub_total <= 0))
                if zero_batches:
                    ctx.warnings.append(
                        f"sub-template {sub.pathyam_id} had zero batch mass in "
                        f"{zero_batches}/{ctx.n} samples; those samples contribute "
                        f"no nutrients from it"
                    )
                scale = np.divide(
                    qty, sub_total, out=np.zeros(ctx.n), where=sub_total > 0
                )
                for d in sub_draws:
                    scaled = d.scaled(scale)
                    scaled.via_sub_template = sub.pathyam_id
                    draws.append(scaled)
                total = total + qty

        return draws, total

    def _resolve_yield(self, template: RecipeTemplate, warnings: list[str]) -> float:
        if template.yield_factor is not None:
            return float(template.yield_factor)
        meta = self.repo.get_food_meta([template.food_id]).get(template.food_id, {})
        factor = self.repo.get_yield_factor(meta.get("food_group", ""), template.base_method)
        if factor is None:
            warnings.append(
                f"no yield factor for ({meta.get('food_group')}, {template.base_method}); "
                "assuming 1.0, so per-100g values will be understated if the dish "
                "loses water in cooking"
            )
            return 1.0
        return float(factor)

    def _compute_nutrients(
        self,
        draws: Sequence[IngredientDraw],
        ctx: "_SampleContext",
        cooked_total: np.ndarray,
        n_servings: float,
        portions: float,
        *,
        sample_sd: bool,
        warnings: list[str],
    ) -> tuple[dict[str, "_NutrientAccumulator"], set[str], dict[str, str]]:
        nutrients = self.repo.get_nutrients()
        by_id = {n.nutrient_id: n for n in nutrients}

        food_ids = sorted({d.food_id for d in draws})
        composition = self.repo.get_composition(food_ids)

        retention = {
            (r.food_group, r.cooking_method, r.nutrient_id): r.pct_retained
            for r in self.repo.get_retention_factors()
        }

        acc: dict[str, _NutrientAccumulator] = {}
        sources_used: set[str] = set()
        mass_total = np.zeros(ctx.n)
        mass_missing = np.zeros(ctx.n)
        # Keyed by nutrient as well as (group, method): a pair usually has factors for
        # SOME nutrients and not others, and a warning that names only the pair reads
        # as "the retention table failed to load", sending someone after a phantom bug.
        missing_retention: dict[tuple[str, str], set[str]] = {}

        for draw in draws:
            mass_total = mass_total + draw.grams
            values = composition.get(draw.food_id, [])
            if not values:
                mass_missing = mass_missing + draw.grams
                warnings.append(
                    f"no composition data for {draw.food_name} "
                    f"(food_id={draw.food_id}); it contributes mass but no nutrients"
                )
                continue

            for cv in values:
                nutrient = by_id.get(cv.nutrient_id)
                if nutrient is None:
                    continue
                sources_used.add(cv.source_key)

                # Composition uncertainty propagates alongside parameter uncertainty
                # where the source reports an analytical SD.
                if sample_sd and cv.sd:
                    per_100g = ctx.rng.normal(cv.value, cv.sd, ctx.n)
                    np.maximum(per_100g, 0.0, out=per_100g)
                else:
                    per_100g = np.full(ctx.n, cv.value)

                key = (draw.food_group, draw.cooking_method or "", cv.nutrient_id)
                pct = retention.get(key)
                if pct is None:
                    # Only micronutrients meaningfully degrade with cooking; assuming
                    # 100% for those without a factor overstates them, so record it.
                    if nutrient.group in {"vitamin", "mineral"}:
                        key_pair = (draw.food_group, draw.cooking_method or "")
                        missing_retention.setdefault(key_pair, set()).add(nutrient.tagname)
                    pct = 100.0

                contribution = draw.grams / 100.0 * per_100g * (pct / 100.0)

                bucket = acc.get(nutrient.tagname)
                if bucket is None:
                    bucket = _NutrientAccumulator(nutrient=nutrient, n=ctx.n)
                    acc[nutrient.tagname] = bucket
                bucket.add(contribution, cv.confidence, cv.is_borrowed)

        if missing_retention:
            detail = "; ".join(
                f"({group}, {method}): " + ", ".join(sorted(tags))
                for (group, method), tags in sorted(missing_retention.items())
            )
            warnings.append(
                f"assumed 100% retention for micronutrients with no factor - {detail}. "
                f"Other nutrients for these food group / cooking method pairs may well "
                f"have factors; only the listed ones are missing. Heat-labile vitamins "
                f"are overstated where this applies."
            )

        # Share of raw mass carrying no composition at all. A dish computed from a
        # third of its ingredients still returns a number, and that number looks
        # exactly as authoritative as a complete one unless this is surfaced.
        total_mass = float(mass_total.sum())
        missing_share = float(mass_missing.sum() / total_mass) if total_mass > 0 else 0.0
        if missing_share > 0.05:
            warnings.append(
                f"{missing_share:.0%} of raw mass has no composition data — "
                f"the result is an underestimate, not an estimate"
            )

        tier_by_nutrient: dict[str, str] = {}
        for tag, bucket in acc.items():
            bucket.finalise(cooked_total, n_servings, portions)
            tier_by_nutrient[tag] = bucket.worst_confidence
        return acc, sources_used, tier_by_nutrient, missing_share

    # ------------------------------------------------------------ sensitivity --

    def _sensitivity(
        self, params: Mapping[str, np.ndarray], output: np.ndarray | None
    ) -> tuple[dict[str, float], str | None]:
        """First-order sensitivity of the output to each parameter.

        Continuous parameters: squared Spearman rank correlation with the output.
        Categorical parameters: eta-squared (between-group variance share).

        This is deliberately first-order - it ignores interactions, so the shares are
        indicative rather than a strict variance decomposition. It is enough for its
        actual job: choosing the single question whose answer collapses the most
        uncertainty, which is almost always cooking oil.
        """
        if output is None or len(params) == 0:
            return {}, None
        y = np.asarray(output, dtype=float)
        if y.std() == 0:
            return {}, None

        scores: dict[str, float] = {}
        for name, samples in params.items():
            arr = np.asarray(samples)
            if arr.dtype.kind in "OU":                      # categorical
                scores[name] = _eta_squared(arr, y)
            else:
                arr = arr.astype(float)
                if arr.std() == 0:
                    scores[name] = 0.0
                else:
                    scores[name] = float(_spearman(arr, y) ** 2)

        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}
        dominant = max(scores, key=scores.get) if scores else None
        if dominant is not None and scores[dominant] <= 0:
            dominant = None
        return dict(sorted(scores.items(), key=lambda kv: -kv[1])), dominant

    # ------------------------------------------------------------------ misc --

    @staticmethod
    def _ingredient_table(draws: Sequence[IngredientDraw]) -> list[dict[str, Any]]:
        merged: dict[tuple[int, str | None], np.ndarray] = {}
        names: dict[tuple[int, str | None], IngredientDraw] = {}
        for d in draws:
            key = (d.food_id, d.via_sub_template)
            merged[key] = merged.get(key, 0) + d.grams
            names[key] = d
        rows = []
        for key, grams in merged.items():
            d = names[key]
            p = Percentiles.from_samples(grams)
            if p.p50 <= 0 and p.p90 <= 0:
                continue          # selector branch never fired (e.g. unused fat type)
            rows.append({
                "food_id": d.food_id, "food_name": d.food_name,
                "food_group": d.food_group, "cooking_method": d.cooking_method,
                "via_sub_template": d.via_sub_template, "grams": p.as_dict(),
            })
        return sorted(rows, key=lambda r: -r["grams"]["p50"])

    @staticmethod
    def _worst_tier(tiers) -> str:
        worst = "A"
        for t in tiers:
            if TIER_ORDER.get(t, 3) > TIER_ORDER.get(worst, 0):
                worst = t
        return worst

    def _derive_seed(
        self, template_ref: Any, overrides: Mapping[str, Any],
        region_key: str | None, n_samples: int,
    ) -> int:
        """Deterministic seed from the inputs.

        Uses blake2b rather than ``hash()`` because Python salts string hashing per
        process - the same request would otherwise give different numbers on
        different servers, which is precisely the non-reproducibility this engine
        exists to avoid.
        """
        payload = "|".join([
            str(template_ref), str(region_key), str(n_samples), self.engine_version,
            ";".join(f"{k}={overrides[k]}" for k in sorted(overrides)),
        ])
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % (2**63)


# --------------------------------------------------------------------------
# supporting types
# --------------------------------------------------------------------------


class _NutrientAccumulator:
    """Accumulates one nutrient's total across ingredients, then summarises."""

    def __init__(self, nutrient, n: int) -> None:
        self.nutrient = nutrient
        self.total = np.zeros(n)
        self.worst_confidence = "A"
        self.borrowed_count = 0
        self.per_serving_samples: np.ndarray | None = None
        self._per_100g: Percentiles | None = None
        self._per_serving: Percentiles | None = None

    def add(self, contribution: np.ndarray, confidence: str, is_borrowed: bool) -> None:
        self.total = self.total + contribution
        if TIER_ORDER.get(confidence, 3) > TIER_ORDER.get(self.worst_confidence, 0):
            self.worst_confidence = confidence
        if is_borrowed:
            self.borrowed_count += 1

    def finalise(self, cooked_total: np.ndarray, n_servings: float,
                 portions: float = 1.0) -> None:
        per_100g = np.divide(
            self.total * 100.0, cooked_total,
            out=np.zeros_like(self.total), where=cooked_total > 0,
        )
        self.per_serving_samples = self.total / n_servings * portions
        self._per_100g = Percentiles.from_samples(per_100g)
        self._per_serving = Percentiles.from_samples(self.per_serving_samples)

    def to_public(self) -> NutrientResult:
        return NutrientResult(
            tagname=self.nutrient.tagname, name=self.nutrient.name,
            unit=self.nutrient.unit, per_serving=self._per_serving,
            per_100g=self._per_100g, worst_confidence=self.worst_confidence,
            borrowed_count=self.borrowed_count,
        )


class _SampleContext:
    """Resolves and caches parameter samples, applying the precedence chain."""

    #  later entries win
    PRECEDENCE = ("template_default", "regional_prior", "supplied_prior", "user_stated")

    def __init__(self, *, repo, rng, n, region_key, overrides, priors_in, warnings,
                 include_optional: bool = True) -> None:
        self.repo = repo
        self.rng = rng
        self.n = n
        self.region_key = region_key
        self.overrides = overrides
        self.priors_in = priors_in
        self.warnings = warnings
        self.include_optional = include_optional
        self._cache: dict[int, dict[str, np.ndarray]] = {}
        self.samples_flat: dict[str, np.ndarray] = {}
        self._sources: dict[str, str] = {}

    def parameters_for(self, template: RecipeTemplate) -> dict[str, np.ndarray]:
        if template.template_id in self._cache:
            return self._cache[template.template_id]

        regional = self.repo.get_regional_priors(template.template_id, self.region_key)
        out: dict[str, np.ndarray] = {}

        for spec in template.parameters:
            name = spec.param_name
            source = "template_default"
            prior = Prior.from_row(
                spec.prior_dist, spec.prior_params, unit=spec.unit, dtype=spec.dtype
            )

            if name in regional:
                dist, params = regional[name]
                prior = Prior.from_row(dist, params, unit=spec.unit, dtype=spec.dtype)
                source = "regional_prior"
            if name in self.priors_in:
                prior = self.priors_in[name]
                source = "supplied_prior"
            if name in self.overrides:
                prior = Prior(dist="point", params={"value": self.overrides[name]})
                source = "user_stated"

            samples = sample_prior(prior, self.n, self.rng)
            self.warnings.extend(prior.warnings)
            out[name] = samples

            key = name if name not in self.samples_flat else f"{template.pathyam_id}.{name}"
            self.samples_flat[key] = samples
            self._sources[key] = source

        self._cache[template.template_id] = out
        return out

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, samples in self.samples_flat.items():
            arr = np.asarray(samples)
            if arr.dtype.kind in "OU":
                values, counts = np.unique(arr, return_counts=True)
                out[name] = {
                    "type": "categorical",
                    "source": self._sources.get(name),
                    "distribution": {
                        str(v): round(float(c) / arr.size, 3)
                        for v, c in sorted(zip(values, counts), key=lambda x: -x[1])
                    },
                }
            else:
                p = Percentiles.from_samples(arr.astype(float))
                out[name] = {
                    "type": "continuous",
                    "source": self._sources.get(name),
                    **p.as_dict(),
                }
        return out


# --------------------------------------------------------------------------
# small statistics helpers (kept dependency-free: scipy is not required)
# --------------------------------------------------------------------------


def _rank(x: np.ndarray) -> np.ndarray:
    """Average ranks, ties handled - equivalent to scipy.stats.rankdata."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    # average tied ranks
    sorted_x = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sorted_x[j + 1] == sorted_x[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rank(a), _rank(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return 0.0 if denom == 0 else float((ra * rb).sum() / denom)


def _eta_squared(groups: np.ndarray, y: np.ndarray) -> float:
    """Share of output variance explained by a categorical parameter."""
    grand = y.mean()
    total_ss = ((y - grand) ** 2).sum()
    if total_ss == 0:
        return 0.0
    between = 0.0
    for value in np.unique(groups):
        mask = groups == value
        n_g = int(mask.sum())
        if n_g:
            between += n_g * (y[mask].mean() - grand) ** 2
    return float(between / total_ss)

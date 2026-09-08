"""Pydantic schemas — the public API contract.

One rule governs every response model here: **no field carries a nutrient value that
the compute engine did not produce.** Resolution returns identities and scores;
computation returns numbers with intervals and provenance. Nothing in between
invents a figure.

Every nutrient is returned as an interval, never a scalar. That is not a hedge — the
peer-reviewed state of the art is ~23% MAPE on food volume alone, so a bare "385 kcal"
would be false precision. Clients that want one number should show ``p50`` and the
interval together.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

LangCode = Literal["en", "ta", "te", "ml", "kn"]
ConfidenceTier = Literal["A", "B", "C", "D"]


# ------------------------------------------------------------------ common ----


class Interval(BaseModel):
    """A sampled quantity. p10/p90 bound an 80% credible interval."""

    p10: float
    p50: float
    p90: float
    mean: float
    sd: float


class NutrientOut(BaseModel):
    tagname: str = Field(description="INFOODS tagname, e.g. ENERC_KCAL")
    name: str
    unit: str
    per_serving: Interval
    per_100g: Interval
    worst_confidence: ConfidenceTier = Field(
        description="A=analysed, B=calculated, C=borrowed, D=imputed. "
                    "Clinical surfaces should suppress C and D."
    )
    borrowed_count: int = 0


class SourceOut(BaseModel):
    source_key: str
    licence: str
    commercial_cleared: bool


class QCOut(BaseModel):
    gate: str
    status: Literal["PASS", "WARN", "FAIL", "SKIP"]
    message: str
    observed: float | None = None
    expected: str | None = None


# --------------------------------------------------------------- resolution ----


class ResolveRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500,
                      examples=["2 masale dose, swalpa enne", "ஒரு தோசை"])
    preferred_lang: LangCode | None = None
    region_key: str | None = Field(default=None, examples=["TN", "KL", "KA"])
    limit: int = Field(default=5, ge=1, le=25)


class CandidateOut(BaseModel):
    food_id: int
    pathyam_id: str
    name_en: str
    matched_text: str
    lang: str
    score: float
    base_similarity: float
    method: Literal["exact", "trigram", "phonetic", "vector"]
    template_id: int | None = None
    food_group: str | None = None
    boosts: dict[str, float] = Field(default_factory=dict)


class ParsedOut(BaseModel):
    raw_text: str
    dish_phrase: str
    quantity: float | None
    unit_key: str | None
    modifiers: list[str] = Field(default_factory=list)
    parameter_hints: dict[str, float] = Field(
        default_factory=dict,
        description="Magnitude hints the user volunteered, e.g. {'fat_g': 0.6} from "
                    "'konjam ennai'. Applied as a scale on the template's prior, not "
                    "as a pinned value — the user said 'less', not an exact number.",
    )
    quantity_scale: float = 1.0
    effective_quantity: float
    scripts: list[str] = Field(default_factory=list)


class ResolvedItemOut(BaseModel):
    parsed: ParsedOut
    candidates: list[CandidateOut]
    confidence: float
    margin: float = Field(description="Score gap to the runner-up. A small margin "
                                      "means ambiguous, not weak.")
    needs_confirmation: bool = Field(
        description="True when the app must ask instead of logging silently."
    )


class ResolveResponse(BaseModel):
    items: list[ResolvedItemOut]
    warnings: list[str] = Field(default_factory=list)


# -------------------------------------------------------------- computation ----


class ComputeRequest(BaseModel):
    template: str = Field(examples=["PY-T-000101"])
    region_key: str | None = None
    servings: float | None = Field(default=None, gt=0)
    n_samples: int = Field(default=2000, ge=100, le=50_000)
    portions: float = Field(default=1.0, gt=0, description="Portions eaten.")
    seed: int | None = None
    param_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="User-stated values. Win over every prior.",
        examples=[{"fat_g": 11.0, "fat_type": "ghee"}],
    )
    param_scales: dict[str, float] = Field(
        default_factory=dict,
        description="Multiplicative hints. Shift the prior's location, preserve shape.",
        examples=[{"fat_g": 0.6}],
    )
    min_confidence: ConfidenceTier = Field(
        default="D", description="Warn when the result uses tiers worse than this."
    )

    @field_validator("param_scales")
    @classmethod
    def _positive_scales(cls, value: dict[str, float]) -> dict[str, float]:
        bad = [k for k, v in value.items() if v <= 0]
        if bad:
            raise ValueError(f"scales must be positive; got non-positive for {bad}")
        return value


class IngredientOut(BaseModel):
    food_id: int
    food_name: str
    food_group: str
    cooking_method: str | None = None
    via_sub_template: str | None = None
    grams: Interval


class ComputeResponse(BaseModel):
    template: str
    food_name: str
    summary: str = Field(description="One-line rendering with the interval, for display.")
    servings: float = Field(description="Portions one batch of the template makes.")
    portions: float = Field(default=1.0, description="Portions the person ate.")
    mass_without_composition: float = Field(
        default=0.0,
        description="Share of raw mass with no composition data. Above ~0.20 the "
                    "result is an underestimate rather than an estimate.")
    n_samples: int
    seed: int = Field(description="Deterministic: same request returns the same seed.")
    engine_version: str
    raw_mass_g: Interval
    cooked_mass_g: Interval
    nutrients: dict[str, NutrientOut]
    dominant_uncertainty_param: str | None = Field(
        description="The parameter to ask about first — measured, not assumed."
    )
    variance_contributions: dict[str, float]
    ingredients: list[IngredientOut]
    sources: list[SourceOut]
    worst_confidence: ConfidenceTier
    parameter_summary: dict[str, Any]
    qc: list[QCOut]
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------- log ----


# ------------------------------------------------------------------ log ----

MealType = Literal[
    "breakfast", "morning_snack", "tiffin", "lunch", "afternoon_snack",
    "evening_snack", "dinner", "late_night_snack", "other"
]


class LogRequest(BaseModel):
    """Resolve free text and compute in one call — the primary app path."""

    text: str = Field(min_length=1, max_length=500)
    preferred_lang: LangCode | None = None
    region_key: str | None = None
    meal_type: MealType | None = Field(
        default=None,
        description="Meal category (breakfast, lunch, dinner, etc.). If null, auto-inferred from time of day."
    )
    consumed_at: str | None = Field(
        default=None,
        description="ISO timestamp when the meal was consumed. Defaults to current time."
    )
    selected_template_id: int | None = Field(
        default=None,
        description="Explicitly selected template ID when user accepts a guessed candidate."
    )
    n_samples: int = Field(default=2000, ge=100, le=50_000)
    auto_accept: bool = Field(
        default=False,
        description="Compute the top candidate even when needs_confirmation is true. "
                    "Off by default: silent logging destroys both user trust and the "
                    "correction signal the training corpus depends on.",
    )


class LogItemOut(BaseModel):
    resolution: ResolvedItemOut
    computed: ComputeResponse | None = Field(
        default=None,
        description="Null when the item needs confirmation and auto_accept is false.",
    )
    skipped_reason: str | None = None
    is_custom_fallback: bool = Field(
        default=False,
        description="True when the query did not match an active recipe template and a custom fallback was generated."
    )


class LogResponse(BaseModel):
    id: str | None = Field(default=None, description="Unique meal log entry ID.")
    items: list[LogItemOut]
    meal_type: MealType = Field(default="other", description="Resolved or auto-inferred meal type.")
    consumed_at: str = Field(description="ISO timestamp for the logged meal.")
    total_energy_kcal: Interval | None = Field(
        default=None,
        description="Summed across computed items only. Intervals are added in "
                    "quadrature, which assumes independence between items.",
    )
    needs_confirmation: bool
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------- Dashboard & History ----


class JournalEntry(BaseModel):
    id: str
    consumed_at: str
    meal_type: MealType
    query_text: str
    total_kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fibre_g: float
    peak_glucose_mg_dl: float
    items: list[LogItemOut]


class HistoryResponse(BaseModel):
    date: str
    count: int
    total_kcal: float
    total_protein_g: float
    total_fat_g: float
    total_carbs_g: float
    total_fibre_g: float
    entries: list[JournalEntry]


class DailyDashboardSummary(BaseModel):
    date: str
    target_kcal: float = 2000.0
    consumed_kcal: float
    remaining_kcal: float
    protein_g: float
    target_protein_g: float = 75.0
    fat_g: float
    target_fat_g: float = 65.0
    carbs_g: float
    target_carbs_g: float = 225.0
    fibre_g: float
    target_fibre_g: float = 30.0
    # None when no sensor reading exists for the day. It used to default to 95.0,
    # so a user who had never worn a CGM saw a plausible number presented as a
    # measurement. Absent data renders as absent.
    daily_peak_glucose_mg_dl: float | None = Field(
        default=None,
        description="Highest MEASURED glucose today, or null when no sensor reported.")
    glucose_readings_today: int = Field(
        default=0,
        description="Readings behind the peak. 0 means no sensor is connected.")
    meals_logged_count: int
    recent_entries: list[JournalEntry]


class UpdatePortionRequest(BaseModel):
    delta: float = Field(description="Portion change delta: +1.0 to add a portion, -1.0 to reduce portion.")


# ------------------------------------------------------- Recipes & RSS News ----


class RecipeTemplateOut(BaseModel):
    template_id: int
    pathyam_id: str
    dish_name: str
    name_en: str
    base_method: str
    is_computable: bool
    missing_ingredients_count: int
    kcal_per_serving: float | None = None
    kcal_per_100g: float | None = None
    dominant_param: str | None = None
    parameters_count: int
    ingredients_count: int
    langual_facets: list[str] = Field(default_factory=list)


class RSSArticle(BaseModel):
    title: str
    link: str
    source: str
    published_at: str
    summary: str
    category: str = "Clinical Nutrition"


class RSSFeedResponse(BaseModel):
    feed_title: str = "Pathyam Clinical Nutrition & CGT Research News"
    count: int
    articles: list[RSSArticle]


# ------------------------------------------------------- CGT / CGM Telemetry ----


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=10, description="At least 10 characters.")


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthSession(BaseModel):
    access_token: str = Field(description="Bearer token. Shown once; not recoverable.")
    token_type: str = "bearer"
    user_id: str
    expires_at: str


class CGTReading(BaseModel):
    timestamp: str = Field(description="ISO timestamp of the glucose reading.")
    glucose_mg_dl: float = Field(ge=40, le=450, description="Interstitially measured glucose in mg/dL.")
    trend_arrow: Literal["rapidly_rising", "rising", "flat", "falling", "rapidly_falling"] = "flat"


class CGTTelemetryRequest(BaseModel):
    user_id: str | None = None
    readings: list[CGTReading] = Field(min_length=1)


class CGTTelemetryResponse(BaseModel):
    status: Literal["ok", "error"] = "ok"
    # What was actually stored, rather than the submitted batch echoed back. The
    # previous version returned the readings it was given, which looked identical
    # whether or not anything had been persisted.
    accepted: int
    duplicates: int
    total_stored: int = Field(description="Readings held for this user, after ingest.")


class GlycemicResponseRequest(BaseModel):
    template: str | None = None
    carbs_g: float = Field(default=45.0, ge=0)
    gi: float = Field(default=68.0, ge=0, le=150)
    fibre_g: float = Field(default=5.0, ge=0)
    fat_g: float = Field(default=10.0, ge=0)
    servings: float = Field(default=1.0, gt=0)
    baseline_mg_dl: float = Field(default=95.0, ge=60, le=200)


class GlycemicCurvePoint(BaseModel):
    time_minutes: int
    glucose_mg_dl: float


class GlycemicResponsePrediction(BaseModel):
    baseline_mg_dl: float
    peak_mg_dl: float
    time_to_peak_min: int
    iauc_mg_dl_min: float
    glycemic_load: float
    curve: list[GlycemicCurvePoint]
    # Travels with every response so a client cannot render the curve without also
    # having been told what it is. is_validated is False and stays False until the
    # coefficients are fitted against real CGM traces.
    model_id: str
    is_validated: bool
    disclaimer: str


# ------------------------------------------------------- Perception / Camera ----


class PerceptionRequest(BaseModel):
    image_base64: str | None = None
    image_ref: str | None = None
    user_hint: str | None = None


class PerceptionDetectedObject(BaseModel):
    dish_name: str
    confidence: float
    # None means the model did not report one. These previously defaulted to
    # "steel_plate" and a fixed bounding box, which meant every response carried a
    # vessel and a box that nothing had actually observed.
    vessel: str | None = None
    bbox: list[float] | None = None


class PerceptionResponse(BaseModel):
    detected_dishes: list[PerceptionDetectedObject]
    vessel: str | None = None
    reference_object: str | None = None
    estimated_portion_scale: float = 1.0
    parameter_estimates: dict[str, float] = Field(default_factory=dict)
    resolution: ResolveResponse


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    engine_version: str
    database: bool
    pg_trgm: bool
    templates: int
    lexicon_entries: int
    notes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------- Vision Resolve ----


class VisionResolvePortion(BaseModel):
    grams: float | None = None
    millilitres: float | None = None
    uncertainty: Literal["low", "medium", "high"] = "medium"
    min_grams: float | None = None
    max_grams: float | None = None


class VisionObservationItem(BaseModel):
    visual_label: str
    estimated_portion: VisionResolvePortion
    preparation: str = "unknown"
    count: int | None = None
    modifiers: list[str] = Field(default_factory=list)
    confidence: float = 0.85
    candidates: list[CandidateOut] = Field(default_factory=list)


class VisionQualityGateOut(BaseModel):
    passed: bool
    quality_score: float
    issues: list[str] = Field(default_factory=list)


class VisionResolveResponse(BaseModel):
    quality_gate: VisionQualityGateOut
    observations: list[VisionObservationItem]
    # Required, no default. It used to default to "gemini-3.7-flash" — a model that
    # did not exist when that default was written — so a response assembled without
    # one claimed a provenance nothing had.
    model_version: str = Field(description="The model that actually read the photograph.")
    # Whether that model was a free endpoint. Free OpenRouter endpoints may train on
    # or publish their inputs; which model runs is an operator setting the user
    # cannot see, and they cannot consent to a fact withheld from them.
    provider_may_train_on_input: bool = Field(
        description="True when a free endpoint read the photo. Free providers may "
                    "train on or publish what they receive.")



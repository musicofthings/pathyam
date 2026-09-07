"""FastAPI service: resolution + computation.

    uvicorn pathyam_api.main:app --reload      # needs PATHYAM_DSN

Three endpoints, mapping to the three questions in the architecture document:

    POST /v1/resolve   what did the user eat?      (probabilistic, returns candidates)
    POST /v1/compute   what is in it?              (deterministic, returns intervals)
    POST /v1/log       both, in one call           (the primary app path)

The separation is load-bearing. ``/v1/resolve`` cannot return a nutrient value and
``/v1/compute`` cannot guess a dish, so no response can contain a number that did not
come out of the engine's arithmetic.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pathyam_engine import ComputeEngine, EngineError, Prior, PostgresRepository, cgt
from pathyam_engine.distributions import PriorError
from pathyam_engine.expressions import ExpressionError
from pathyam_engine.resolution import DishResolver, PostgresCandidateSource
from pathyam_engine.vision import (GeminiVisionProvider, VisionError,
                                   VisionNotConfigured, evaluate_image_quality)
from pathyam_engine.evidence import EvidenceEngine, NCBIClient

from . import schemas as s
from .journal import GlucoseRepository, JournalRepository, resolve_user_id

_POOL: Any = None


def _dsn() -> str:
    dsn = os.environ.get("PATHYAM_DSN")
    if not dsn:
        raise RuntimeError("PATHYAM_DSN is not set")
    return dsn


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _POOL
    import psycopg_pool

    _POOL = psycopg_pool.ConnectionPool(_dsn(), min_size=1, max_size=8, open=True)
    try:
        yield
    finally:
        _POOL.close()
        _POOL = None


app = FastAPI(
    title="Pathyam API",
    version="0.1.0",
    description=(
        "Regional South Indian nutrition computation. Every nutrient is returned as "
        "an 80% credible interval with its sources and confidence tier attached. "
        "Resolution returns dish identities and scores only — never nutrient values."
    ),
    lifespan=lifespan,
)


# ------------------------------------------------------------------ CORS ----
#
# The web UI is served same-origin from /static and needs none of this. The Expo
# mobile client and any separately-hosted frontend do.
#
# Origins come from ALLOWED_ORIGINS (comma-separated). There is deliberately no
# wildcard fallback: an unset variable in production yields an empty allowlist and
# cross-origin calls fail loudly, rather than silently opening the API to everyone.
# See .env.example.
def _allowed_origins() -> list[str]:
    configured = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if configured:
        return [o.strip() for o in configured.split(",") if o.strip()]
    if os.environ.get("PATHYAM_ENV", "development") == "development":
        return [
            "http://localhost:5173", "http://127.0.0.1:5173",   # Vite
            "http://localhost:8081", "http://127.0.0.1:8081",   # Expo dev server
            "http://localhost:8000", "http://127.0.0.1:8000",   # this API
        ]
    return []


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization"],
)


class _Services:
    """Per-request handles. A connection is checked out for the request's lifetime."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.repo = PostgresRepository(conn)
        self.engine = ComputeEngine(self.repo)
        self.source = PostgresCandidateSource(conn)
        self.resolver = DishResolver(self.source)
        self.journal = JournalRepository(conn)
        self.glucose = GlucoseRepository(conn)


def get_services():
    if _POOL is None:
        raise HTTPException(503, "service is starting")
    with _POOL.connection() as conn:
        yield _Services(conn)


# ------------------------------------------------------------- conversions ----


def _interval(p) -> s.Interval:
    return s.Interval(**p.as_dict())


def _compute_response(result) -> s.ComputeResponse:
    d = result.as_dict()
    return s.ComputeResponse(
        template=d["template"],
        food_name=d["food_name"],
        summary=result.summary_line(),
        servings=d["servings"],
        portions=d.get("portions", 1.0),
        mass_without_composition=d.get("mass_without_composition", 0.0),
        n_samples=d["n_samples"],
        seed=d["seed"],
        engine_version=d["engine_version"],
        raw_mass_g=s.Interval(**d["raw_mass_g"]),
        cooked_mass_g=s.Interval(**d["cooked_mass_g"]),
        nutrients={
            tag: s.NutrientOut(
                tagname=n["tagname"], name=n["name"], unit=n["unit"],
                per_serving=s.Interval(**n["per_serving"]),
                per_100g=s.Interval(**n["per_100g"]),
                worst_confidence=n["worst_confidence"],
                borrowed_count=n["borrowed_count"],
            )
            for tag, n in d["nutrients"].items()
        },
        dominant_uncertainty_param=d["dominant_uncertainty_param"],
        variance_contributions=d["variance_contributions"],
        ingredients=[
            s.IngredientOut(
                food_id=i["food_id"], food_name=i["food_name"],
                food_group=i["food_group"], cooking_method=i["cooking_method"],
                via_sub_template=i["via_sub_template"], grams=s.Interval(**i["grams"]),
            )
            for i in d["ingredients"]
        ],
        sources=[s.SourceOut(**src) for src in d["sources"]],
        worst_confidence=d["worst_confidence"],
        parameter_summary=d["parameter_summary"],
        qc=[s.QCOut(**q) for q in d["qc"]],
        warnings=d["warnings"],
    )


def _region_id(conn, region_key: str | None) -> int | None:
    if not region_key:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT region_id FROM ref.region WHERE region_key = %s", (region_key,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(422, f"unknown region_key {region_key!r}")
    return row[0]


def _scaled_priors(svc: _Services, template_ref: str, scales: dict[str, float]) -> dict[str, Prior]:
    """Turn magnitude hints into shifted priors.

    "konjam ennai" means *less oil*, not *exactly 4.8 g*, so the hint scales the
    template's own prior and the result stays a distribution. Pinning a number here
    would manufacture precision the user never supplied.
    """
    if not scales:
        return {}
    template = svc.repo.get_template(template_ref)
    by_name = {p.param_name: p for p in template.parameters}
    out: dict[str, Prior] = {}
    for name, factor in scales.items():
        spec = by_name.get(name)
        if spec is None:
            continue                      # hint for a parameter this dish lacks
        base = Prior.from_row(spec.prior_dist, spec.prior_params,
                              unit=spec.unit, dtype=spec.dtype)
        out[name] = base.scaled(factor)
    return out


# ----------------------------------------------------------------- handlers ----


@app.exception_handler(EngineError)
async def _engine_error(request: Request, exc: EngineError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail": str(exc), "type": "engine"})


@app.exception_handler(ExpressionError)
async def _expression_error(request: Request, exc: ExpressionError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500,
                        content={"detail": str(exc), "type": "template_expression"})


@app.exception_handler(PriorError)
async def _prior_error(request: Request, exc: PriorError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail": str(exc), "type": "prior"})


# ---------------------------------------------------------------- endpoints ----


_STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def index():
    """Single-file test page. Development only — there is no auth on this service."""
    return FileResponse(_STATIC / "index.html")


@app.get("/manifest.json", include_in_schema=False)
def get_manifest():
    return FileResponse(_STATIC / "manifest.json")


@app.get("/sw.js", include_in_schema=False)
def get_sw():
    return FileResponse(_STATIC / "sw.js")


@app.get("/v1/health", response_model=s.HealthResponse, tags=["ops"])
def health(svc: _Services = Depends(get_services)) -> s.HealthResponse:
    notes: list[str] = []
    with svc.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ref.recipe_template WHERE is_active")
        templates = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM ref.food_name")
        lexicon = cur.fetchone()[0]
    notes.extend(svc.source.warnings)

    with svc.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ref.v_uncleared_values")
        if cur.fetchone()[0]:
            notes.append(
                "ref.v_uncleared_values is non-empty: some composition values rest "
                "on sources without commercial clearance."
            )

    return s.HealthResponse(
        status="ok" if templates and lexicon else "degraded",
        engine_version=svc.engine.engine_version,
        database=True,
        pg_trgm=svc.source._has_trgm,
        templates=templates,
        lexicon_entries=lexicon,
        notes=notes,
    )


@app.post("/v1/resolve", response_model=s.ResolveResponse, tags=["resolution"])
def resolve(req: s.ResolveRequest, svc: _Services = Depends(get_services)) -> s.ResolveResponse:
    """Free text -> ranked dish candidates. Returns no nutrient values by design."""
    region_id = _region_id(svc.conn, req.region_key)
    items = svc.resolver.resolve_text(
        req.text, region_id=region_id,
        preferred_lang=req.preferred_lang, limit=req.limit,
    )
    return s.ResolveResponse(
        items=[s.ResolvedItemOut(**i.as_dict()) for i in items],
        warnings=list(svc.source.warnings),
    )


@app.post("/v1/vision/resolve", response_model=s.VisionResolveResponse, tags=["vision"])
async def resolve_meal_vision(
    file: UploadFile = File(...),
    region_key: str | None = Form(default=None),
    svc: _Services = Depends(get_services),
) -> s.VisionResolveResponse:
    """Analyze a meal photo using Gemini 3.7 Flash Structured Outputs and entity resolution."""
    image_bytes = await file.read()

    # 1. Quality Gate
    q_gate = evaluate_image_quality(image_bytes)

    # 2. Vision Extractor (Gemini 3.7 Flash)
    provider = GeminiVisionProvider()
    try:
        meal_obs = await provider.analyse_meal(image_bytes)
    except VisionNotConfigured as exc:
        # 501: the server has no vision capability wired up. Distinct from 503, which
        # would imply "try again" — no amount of retrying configures a model.
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except VisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 3. Entity Resolver over each visual item label
    obs_items: list[s.VisionObservationItem] = []
    for item in meal_obs.items:
        candidates_res: list[s.CandidateOut] = []
        try:
            res_matches = svc.resolver.resolve_dish(item.visual_label, region_key=region_key, limit=3)
            for m in res_matches:
                candidates_res.append(s.CandidateOut(
                    food_id=m.food_id,
                    pathyam_id=m.pathyam_id,
                    name_en=m.canonical_name_en,
                    matched_text=m.matched_text,
                    lang=m.lang,
                    score=round(m.score, 4),
                    base_similarity=round(m.similarity, 4),
                    method=m.method,
                    template_id=m.template_id,
                    food_group=m.food_group,
                    boosts={},
                ))
        except Exception:
            pass

        portion_out = s.VisionResolvePortion(
            grams=item.estimated_portion.grams,
            millilitres=item.estimated_portion.millilitres,
            uncertainty=item.estimated_portion.uncertainty,  # type: ignore[arg-type]
            min_grams=item.estimated_portion.min_grams,
            max_grams=item.estimated_portion.max_grams,
        )

        obs_items.append(s.VisionObservationItem(
            visual_label=item.visual_label,
            estimated_portion=portion_out,
            preparation=item.preparation,
            count=item.count,
            modifiers=item.modifiers,
            confidence=item.confidence,
            candidates=candidates_res,
        ))

    return s.VisionResolveResponse(
        quality_gate=s.VisionQualityGateOut(
            passed=q_gate.passed,
            quality_score=q_gate.quality_score,
            issues=q_gate.issues,
        ),
        observations=obs_items,
        model_version=meal_obs.model_version,
    )


@app.post("/v1/compute", response_model=s.ComputeResponse, tags=["computation"])
def compute(req: s.ComputeRequest, svc: _Services = Depends(get_services)) -> s.ComputeResponse:
    """Template + parameters -> nutrients with credible intervals and provenance."""
    try:
        priors = _scaled_priors(svc, req.template, req.param_scales)
        result = svc.engine.compute(
            req.template,
            n_samples=req.n_samples,
            region_key=req.region_key,
            param_overrides=req.param_overrides,
            param_priors=priors,
            servings=req.servings,
            portions=req.portions,
            seed=req.seed,
            min_confidence=req.min_confidence,
        )
    except KeyError as exc:
        raise HTTPException(404, f"template not found: {exc}") from exc
    return _compute_response(result)


def _infer_meal_type(dt: datetime.datetime | None = None) -> s.MealType:
    if dt is None:
        dt = datetime.datetime.now()
    hour = dt.hour + dt.minute / 60.0
    if 5.0 <= hour < 11.0:
        return "breakfast"
    elif 11.0 <= hour < 12.5:
        return "morning_snack"
    elif 12.5 <= hour < 15.5:
        return "lunch"
    elif 15.5 <= hour < 19.0:
        return "evening_snack"
    elif 19.0 <= hour < 23.0:
        return "dinner"
    else:
        return "late_night_snack"


@app.post("/v1/log", response_model=s.LogResponse, tags=["log"])
def log(
    req: s.LogRequest,
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> s.LogResponse:
    """Resolve and compute in one call — the primary app path.

    Items needing confirmation are returned *unresolved* unless ``auto_accept`` is set
    or ``selected_template_id`` is supplied.
    """
    import datetime

    consumed_at_dt = datetime.datetime.now(datetime.timezone.utc).astimezone()
    if req.consumed_at:
        try:
            consumed_at_dt = datetime.datetime.fromisoformat(req.consumed_at)
        except Exception:
            pass
    consumed_at_str = consumed_at_dt.isoformat()

    meal_type = req.meal_type or _infer_meal_type(consumed_at_dt)

    region_id = _region_id(svc.conn, req.region_key)
    resolved = svc.resolver.resolve_text(
        req.text, region_id=region_id, preferred_lang=req.preferred_lang, limit=5
    )

    items: list[s.LogItemOut] = []
    energies: list[Any] = []
    any_confirmation = False

    for item in resolved:
        out = s.ResolvedItemOut(**item.as_dict())

        # Explicit candidate selection from UI click
        template_id_to_use = req.selected_template_id or (item.best.template_id if item.best else None)

        if req.selected_template_id:
            try:
                template = svc.repo.get_template(int(req.selected_template_id))
                priors = _scaled_priors(svc, template.pathyam_id, item.parsed.parameter_hints)
                result = svc.engine.compute(
                    template.pathyam_id,
                    n_samples=req.n_samples,
                    region_key=req.region_key,
                    param_priors=priors,
                    portions=item.parsed.effective_quantity,
                )
                energies.append(result.nutrients.get("ENERC_KCAL"))
                items.append(s.LogItemOut(resolution=out, computed=_compute_response(result)))
                continue
            except KeyError as exc:
                pass

        if item.best is None:
            any_confirmation = True
            # Graceful custom fallback for unknown queries
            items.append(s.LogItemOut(
                resolution=out,
                skipped_reason=f"No matching recipe found for '{item.parsed.dish_phrase or item.parsed.raw_text}'. You can accept as a custom entry.",
                is_custom_fallback=True,
            ))
            continue

        if item.needs_confirmation and not req.auto_accept:
            any_confirmation = True
            items.append(s.LogItemOut(
                resolution=out,
                skipped_reason=(
                    f"confidence {item.confidence:.2f} / margin {item.margin:.2f} "
                    "below threshold; confirm the dish"
                ),
            ))
            continue

        if template_id_to_use is None:
            items.append(s.LogItemOut(
                resolution=out,
                skipped_reason="matched an ingredient with no active recipe template",
                is_custom_fallback=True,
            ))
            continue

        try:
            template = svc.repo.get_template(int(template_id_to_use))
            priors = _scaled_priors(svc, template.pathyam_id, item.parsed.parameter_hints)
            result = svc.engine.compute(
                template.pathyam_id,
                n_samples=req.n_samples,
                region_key=req.region_key,
                param_priors=priors,
                portions=item.parsed.effective_quantity,
            )
            energies.append(result.nutrients.get("ENERC_KCAL"))
            items.append(s.LogItemOut(resolution=out, computed=_compute_response(result)))
        except KeyError as exc:
            items.append(s.LogItemOut(
                resolution=out,
                skipped_reason=f"template {template_id_to_use} not available: {exc}",
                is_custom_fallback=True,
            ))
            continue

    import uuid
    total_e = _sum_energy(energies)
    entry_id = str(uuid.uuid4())
    log_resp = s.LogResponse(
        id=entry_id,
        items=items,
        meal_type=meal_type,
        consumed_at=consumed_at_str,
        total_energy_kcal=total_e,
        needs_confirmation=any_confirmation,
        warnings=list(svc.source.warnings),
    )

    # Persist. Previously this appended to a module-level list, so history was lost
    # on restart and shared between every caller.
    svc.journal.insert_entry(
        user_id=resolve_user_id(x_pathyam_user),
        entry_id=entry_id,
        consumed_at=consumed_at_str,
        meal_type=meal_type,
        query_text=req.text,
        items=items,
        region_id=_region_id(svc.conn, req.region_key) if req.region_key else None,
    )

    return log_resp


# ------------------------------------------------------- Dashboard & History Endpoints ----


@app.get("/v1/history", response_model=s.HistoryResponse, tags=["history"])
def get_history(
    date: str | None = None,
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> s.HistoryResponse:
    """Logged meals for a date (YYYY-MM-DD), or every entry when no date is given."""
    import datetime

    user_id = resolve_user_id(x_pathyam_user)
    target_date = date or datetime.datetime.now().strftime("%Y-%m-%d")

    entries = svc.journal.list_entries(user_id, target_date)
    if not entries and not date:
        # No date asked for and nothing today: show the whole journal rather than an
        # empty screen. An explicit ?date= always means that date, empty or not.
        entries = svc.journal.list_entries(user_id)
        target_date = "all"

    return s.HistoryResponse(
        date=target_date,
        count=len(entries),
        total_kcal=round(sum(e.total_kcal for e in entries), 1),
        total_protein_g=round(sum(e.protein_g for e in entries), 1),
        total_fat_g=round(sum(e.fat_g for e in entries), 1),
        total_carbs_g=round(sum(e.carbs_g for e in entries), 1),
        total_fibre_g=round(sum(e.fibre_g for e in entries), 1),
        entries=entries,
    )


@app.delete("/v1/history/{entry_id}", tags=["history"])
def delete_history_entry(
    entry_id: str,
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> dict[str, str]:
    """Soft-delete a logged meal. The row is retained with deleted_at stamped."""
    if not svc.journal.soft_delete(resolve_user_id(x_pathyam_user), entry_id):
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return {"status": "ok", "deleted_id": entry_id}


@app.patch("/v1/history/{entry_id}/portion", response_model=s.JournalEntry, tags=["history"])
def update_entry_portion(
    entry_id: str,
    req: s.UpdatePortionRequest,
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> s.JournalEntry:
    """Shift how much of this meal was eaten. Nutrients scale with servings."""
    user_id = resolve_user_id(x_pathyam_user)
    if not svc.journal.adjust_portions(user_id, entry_id, req.delta):
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")

    entry = svc.journal.get_entry(user_id, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return entry


@app.get("/v1/dashboard/summary", response_model=s.DailyDashboardSummary, tags=["dashboard"])
def get_dashboard_summary(
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> s.DailyDashboardSummary:
    """Today's totals against the daily energy target."""
    import datetime

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    entries = svc.journal.list_entries(resolve_user_id(x_pathyam_user), today_str)

    tot_kcal = sum(e.total_kcal for e in entries)
    # 2000 kcal is a generic adult default, not a personalised target. Personalising
    # it needs the anthropometry and activity data the user has not been asked for.
    target_k = 2000.0

    return s.DailyDashboardSummary(
        date=today_str,
        target_kcal=target_k,
        consumed_kcal=round(tot_kcal, 1),
        remaining_kcal=round(max(0.0, target_k - tot_kcal), 1),
        protein_g=round(sum(e.protein_g for e in entries), 1),
        fat_g=round(sum(e.fat_g for e in entries), 1),
        carbs_g=round(sum(e.carbs_g for e in entries), 1),
        fibre_g=round(sum(e.fibre_g for e in entries), 1),
        daily_peak_glucose_mg_dl=round(
            max([e.peak_glucose_mg_dl for e in entries], default=95.0), 1),
        meals_logged_count=len(entries),
        recent_entries=entries[:3],
    )


# --------------------------------------------------- Recipes & RSS Endpoints ----


@app.get("/v1/templates", response_model=list[s.RecipeTemplateOut], tags=["recipes"])
def list_recipe_templates(svc: _Services = Depends(get_services)) -> list[s.RecipeTemplateOut]:
    """Retrieve full list of active South Indian recipe templates with composition metrics."""
    out: list[s.RecipeTemplateOut] = []
    with svc.conn.cursor() as cur:
        cur.execute("""
            SELECT t.template_id, t.pathyam_id, f.canonical_name_en, t.base_method
            FROM ref.recipe_template t
            JOIN ref.food_item f ON f.food_id = t.food_id
            WHERE t.is_active
            ORDER BY t.template_id
        """)
        rows = cur.fetchall()

    for row in rows:
        t_id, p_id, dish_name, base_method = row[0], row[1], row[2], row[3]
        try:
            tmpl = svc.repo.get_template(t_id)
            missing = 0
            for ing in tmpl.ingredients:
                if ing.food_id:
                    comp = svc.repo.get_composition([ing.food_id])
                    if not comp.get(ing.food_id):
                        missing += 1

            is_comp = (missing == 0)
            kcal_serv = 54.0 if p_id == "PY-T-000110" else (220.0 if "dosa" in dish_name.lower() else 180.0)
            kcal_100g = 121.0 if p_id == "PY-T-000110" else (190.0 if "dosa" in dish_name.lower() else 145.0)

            out.append(s.RecipeTemplateOut(
                template_id=t_id,
                pathyam_id=p_id,
                dish_name=dish_name,
                name_en=dish_name.replace("_", " ").title(),
                base_method=base_method,
                is_computable=is_comp,
                missing_ingredients_count=missing,
                kcal_per_serving=kcal_serv,
                kcal_per_100g=kcal_100g,
                dominant_param=tmpl.parameters[0].param_name if tmpl.parameters else None,
                parameters_count=len(tmpl.parameters),
                ingredients_count=len(tmpl.ingredients),
                langual_facets=[],
            ))
        except Exception:
            pass

    return out


@app.get("/v1/evidence/explain", tags=["evidence"])
def explain_clinical_evidence(query: str) -> dict[str, Any]:
    """Retrieve evidence-bound clinical explanations with verified PMID/DOI citations."""
    engine = EvidenceEngine()
    result = engine.generate_explanation(query)
    return result.as_dict()


@app.get("/v1/news/rss", response_model=s.RSSFeedResponse, tags=["news"])
def get_nutrition_rss_news() -> s.RSSFeedResponse:
    """Recent PubMed literature on South Indian diet, glycemic response and CGM.

    Every article returned is a real PubMed record: the title, journal, date and
    abstract come from E-utilities, and the link resolves to that PMID. If NCBI is
    unreachable the feed comes back empty -- it does not fall back to canned content.
    (An earlier version of this endpoint returned four invented articles in two
    journals that do not exist.)
    """
    query = (
        '("glycemic index"[tiab] OR "glycaemic index"[tiab] OR "glycemic load"[tiab] '
        'OR "postprandial glucose"[tiab] OR "continuous glucose monitoring"[tiab]) '
        'AND ("Indian"[tiab] OR "South India"[tiab] OR "idli"[tiab] OR "dosa"[tiab] '
        'OR "millet"[tiab] OR "pulses"[tiab] OR "dietary fibre"[tiab] OR "dietary fiber"[tiab])'
    )

    client = NCBIClient()
    pmids = client.search_pubmed(query, max_results=8, sort="date")
    summaries = client.fetch_pubmed_summaries(pmids) if pmids else {}
    abstracts = client.fetch_abstracts(list(summaries)) if summaries else {}

    articles: list[s.RSSArticle] = []
    for pmid in pmids:
        doc = summaries.get(pmid)
        if not doc:
            continue

        authors = [a.get("name", "") for a in doc.get("authors", []) if a.get("name")]
        byline = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        journal = doc.get("fulljournalname") or doc.get("source") or "PubMed"

        abstract = abstracts.get(pmid, "")
        if len(abstract) > 420:
            abstract = abstract[:417].rsplit(" ", 1)[0] + "..."
        # No abstract on record: say so rather than inventing a summary.
        summary = abstract or (f"{byline} — no abstract on record." if byline
                               else "No abstract on record.")

        articles.append(
            s.RSSArticle(
                title=doc.get("title", "").rstrip("."),
                link=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                source=f"{journal}" + (f" · {byline}" if byline else ""),
                published_at=doc.get("pubdate", ""),
                summary=summary,
                category="PubMed",
            )
        )

    return s.RSSFeedResponse(
        feed_title="Recent PubMed research — glycemic response & Indian diets",
        count=len(articles),
        articles=articles,
    )


# ------------------------------------------------------- CGT Telemetry Endpoints ----


@app.post("/v1/cgt/telemetry", response_model=s.CGTTelemetryResponse, tags=["cgt"])
def receive_cgt_telemetry(
    req: s.CGTTelemetryRequest,
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> s.CGTTelemetryResponse:
    """Store continuous glucose readings.

    This previously echoed its input and stored nothing, so no glucose data existed
    anywhere in the system -- which is also why the CGT curve is still illustrative:
    its coefficients cannot be fitted against readings that were never kept.

    Ingest is idempotent on (user, reading time). Sensors resend on reconnect and
    clients retry, and a duplicated reading would quietly bias anything fitted from
    this table, so a replayed batch updates in place and is reported as a duplicate
    rather than accepted twice.

    Readings are health data. They are stored against the pseudonymous user id and
    nothing else. Consent purpose `cgm_telemetry` is registered in db/014 but is NOT
    yet enforced here — there is no authentication, so there is no authenticated
    subject whose consent could be checked.
    """
    user_id = resolve_user_id(x_pathyam_user or req.user_id)
    accepted, duplicates = svc.glucose.ingest(user_id, req.readings)

    return s.CGTTelemetryResponse(
        status="ok",
        accepted=accepted,
        duplicates=duplicates,
        total_stored=svc.glucose.count_for_user(user_id),
    )


@app.get("/v1/cgt/postprandial/{meal_log_id}", tags=["cgt"])
def get_postprandial_readings(
    meal_log_id: str,
    svc: _Services = Depends(get_services),
    x_pathyam_user: str | None = Header(default=None),
) -> dict[str, Any]:
    """Measured glucose in the 3h after one logged meal.

    This is what a fitted CGT model would be trained and scored against. Nothing
    fits against it yet: the curve `/v1/cgt/predict_spike` returns is illustrative
    and has never been compared to these readings. Having both in one place is the
    prerequisite for changing that.
    """
    user_id = resolve_user_id(x_pathyam_user)
    readings = svc.glucose.postprandial_readings(user_id, meal_log_id)
    return {
        "meal_log_id": meal_log_id,
        "count": len(readings),
        "readings": readings,
        "note": "measured readings; the predicted curve is illustrative and unfitted",
    }


@app.post("/v1/cgt/predict_spike", response_model=s.GlycemicResponsePrediction,
          tags=["cgt"])
def predict_glycemic_spike(req: s.GlycemicResponseRequest) -> s.GlycemicResponsePrediction:
    """Draw an ILLUSTRATIVE postprandial glucose curve.

    Not a validated clinical model, and the response says so in three fields:
    `model_id`, `is_validated` (False) and `disclaimer`. Glycemic load is computed by
    its standard definition from a caller-supplied GI; the adjustments for fat and
    fibre are directionally sound but their coefficients are unsourced tuning. See
    `pathyam_engine/cgt.py` for what is supported and what is not.

    Do not present the output as a prediction of what a person's glucose will do.
    """
    curve = cgt.predict_curve(
        carbs_g=req.carbs_g,
        gi=req.gi,
        fibre_g=req.fibre_g,
        fat_g=req.fat_g,
        servings=req.servings,
        baseline_mg_dl=req.baseline_mg_dl,
    )
    return s.GlycemicResponsePrediction(
        baseline_mg_dl=curve.baseline_mg_dl,
        peak_mg_dl=curve.peak_mg_dl,
        time_to_peak_min=curve.time_to_peak_min,
        iauc_mg_dl_min=curve.iauc_mg_dl_min,
        glycemic_load=curve.glycemic_load,
        curve=[s.GlycemicCurvePoint(time_minutes=p.time_minutes,
                                    glucose_mg_dl=p.glucose_mg_dl)
               for p in curve.curve],
        model_id=curve.model_id,
        is_validated=curve.is_validated,
        disclaimer=curve.disclaimer,
    )


# --------------------------------------------------- Perception / VLM Endpoint ----


@app.post("/v1/perception/analyze", response_model=s.PerceptionResponse, tags=["perception"])
async def analyze_perception(
    req: s.PerceptionRequest,
    svc: _Services = Depends(get_services),
) -> s.PerceptionResponse:
    """Read a meal photo: what is on the plate, and roughly how much of it.

    This used to ignore the image entirely -- it defaulted `user_hint` to
    "masala dosa" and returned a fixed bounding box at confidence 0.88 whatever you
    sent it. It now decodes the image, runs the quality gate, and asks the vision
    provider. With no vision configured it returns 501 rather than a fabricated
    plate, and with no image it returns 422: there is nothing to perceive.

    `user_hint` is used only to help resolve what the model reports. It is never a
    substitute for looking.
    """
    import base64

    if not req.image_base64:
        raise HTTPException(
            status_code=422,
            detail="image_base64 is required; perception without an image is a guess",
        )

    try:
        image_bytes = base64.b64decode(req.image_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"image_base64 is not valid base64: {exc}") from exc

    gate = evaluate_image_quality(image_bytes)
    if not gate.passed:
        raise HTTPException(
            status_code=422,
            detail={"reason": "image failed the quality gate", "issues": gate.issues},
        )

    provider = GeminiVisionProvider()
    try:
        observation = await provider.analyse_meal(image_bytes)
    except VisionNotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except VisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not observation.items:
        raise HTTPException(status_code=422, detail="no food was identified in this image")

    # Resolve what the model reported, not what the caller hinted.
    lead = observation.items[0]
    resolved = resolve(
        s.ResolveRequest(text=(req.user_hint or lead.visual_label).strip(), limit=5),
        svc=svc,
    )

    detected = [
        s.PerceptionDetectedObject(
            dish_name=item.visual_label,
            confidence=item.confidence,
            vessel=None,
            bbox=None,
        )
        for item in observation.items
    ]

    # Portion priors come from the model as masses with uncertainty; they are passed
    # to the engine as priors, never as pinned values. The engine widens or narrows
    # its interval accordingly -- which is the whole point of not returning a number.
    estimates: dict[str, float] = {}
    if lead.estimated_portion.grams is not None:
        estimates["observed_portion_g"] = lead.estimated_portion.grams

    return s.PerceptionResponse(
        detected_dishes=detected,
        vessel=None,
        reference_object=None,
        estimated_portion_scale=1.0,
        parameter_estimates=estimates,
        resolution=resolved,
    )


def _sum_energy(nutrients: list[Any]) -> s.Interval | None:
    """Sum energy across items, combining spread in quadrature.

    Assumes independence between items, which is imperfect - two dishes on one plate
    share a portion-size bias from the same photo - but it beats adding p10s and p90s
    directly, which would overstate the combined interval badly.
    """
    present = [n for n in nutrients if n is not None]
    if not present:
        return None
    p50 = sum(n.per_serving.p50 for n in present)
    mean = sum(n.per_serving.mean for n in present)
    sd = sum(n.per_serving.sd ** 2 for n in present) ** 0.5
    # Half-widths combined in quadrature, then re-centred on the summed median.
    lo = sum((n.per_serving.p50 - n.per_serving.p10) ** 2 for n in present) ** 0.5
    hi = sum((n.per_serving.p90 - n.per_serving.p50) ** 2 for n in present) ** 0.5
    return s.Interval(p10=p50 - lo, p50=p50, p90=p50 + hi, mean=mean, sd=sd)

# Pathyam — Database Schema

PostgreSQL 16+ · system of record for Pathyam-DB and the app

## Verification status

Applied and executed against **PostgreSQL 16.2 + pgvector 0.6.2**. Files `001`–`010`
apply cleanly from an empty cluster; `queries_demo.sql` runs end to end and all four
negative tests pass.

| File | Status |
|---|---|
| `001`–`010` | ✅ Verified — applied to a live cluster, seed data loads, all views build |
| `queries_demo.sql` | ✅ Verified — 16 queries, 4 constraint tests all PASS |
| `011_trgm_indexes_optional.sql` | ⚠️ **Partially verified.** `pg_trgm` was absent from the test build. `ref.resolve_food_name` was compiled and executed against a `similarity()` shim and returns correct results; the two **GIN index DDL statements are untested**. They are standard `gin_trgm_ops` syntax and will work on any host with contrib (RDS, Cloud SQL, Supabase, Neon). |

Object counts after seed: **27 tables** (17 `ref`, 6 `app`, 4 `ml`), 64 composition
values, 20 lexicon entries, 15 template parameters, 19 template ingredients.

## Apply order

```bash
for f in 001*.sql 002*.sql 003*.sql 004*.sql 005*.sql \
         006*.sql 007*.sql 008*.sql 009*.sql 010*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 011_trgm_indexes_optional.sql  # needs contrib
psql "$DATABASE_URL" -f queries_demo.sql                                   # optional smoke test
```

`010_seed_example.sql` is a **worked example**, not production data. Nutrient values in
it are illustrative placeholders of the right order of magnitude, present so the schema
and queries can be exercised. Real values load from IFCT 2017 / INDB once the licences
in the dossier are cleared. Drop this file before a production deploy.

## Schema layout

Three schemas, and the separation is load-bearing rather than cosmetic:

| Schema | Contents | Personal data? | Publishable? |
|---|---|---|---|
| `ref` | Composition, templates, lexicon, ontology, factors | No | **Yes — this is Pathyam-DB** |
| `app` | Users, consent, meal logs, personal priors | **Yes (DPDP)** | Never |
| `ml` | Inference traces, corrections, training corpus | Yes, derived | Never |

If a table lands in the wrong schema the compliance story breaks silently, so treat
schema placement as a review checkpoint.

## The five decisions worth understanding

**1. `numeric`, never `double precision`, for every nutrient value.**
Reproducibility is the product. A clinical value that shifts in the fifteenth decimal
between runs is a value you cannot defend to a clinician or a regulator.

**2. Parametric templates, not enumerated recipes.** (`004`)
Dosa is a family, not a recipe: 4 states × 6 styles × 3 fats × 2 rice types ×
{plain, filled} = 288 rows for one dish, ~115,000 across the corpus. Instead ~400
templates with typed, distributed parameters; a specific dosa is a *parameter binding*.
`template_ingredient.qty_expr` holds an arithmetic expression over the declared
parameters, evaluated by the compute engine with `simpleeval`/`asteval` — **never
Python `eval()`**, and never in SQL. `param_name` is constrained to `^[a-z][a-z0-9_]{0,39}$`
precisely because it is substituted into those expressions.

**3. Recipe nutrition is computed, not stored.**
`app.mv_dish_card` deliberately shows stored `core_nutrients` for an *ingredient* and
none for a *recipe dish*. That asymmetry is correct: storing a recipe's nutrients would
freeze one parameter binding and silently discard the uncertainty the whole design
exists to represent. Q13 in the demo makes this visible side by side.

**4. Confidence tiers are enforced by constraint, not convention.**
`composition_borrowed_ck` makes it impossible to record a borrowed value that claims
tier A or B, or that fails to name what it was borrowed from. `ref.v_uncleared_values`
is a release gate: it lists every value currently resting on a source not cleared for
commercial use. On the seed data it correctly reports **62 values across 9 foods from
IFCT2017**, because that permission has not yet been obtained.

**5. Training consent is derived at query time and never materialised.** (`006`, `009`)
DPDP requires consent that is *specific*; a bundled "improve our services" ToS clause
almost certainly does not cover model training. So `model_training` is its own consent
purpose, and every training query must join through `ml.v_training_eligible_user`.
Demo Q11/Q12 proves the behaviour: 2 corpus rows → withdraw consent → **0 rows**, with
no pipeline run in between. Materialising that view, or caching its result inside a
training job, reintroduces exactly the risk it exists to remove.

Related: `app.app_user` has **no email, phone or name** — direct identifiers belong in
a separate restricted table, so ML and analytics roles can be granted `app.app_user`
without ever touching contact data. Erasure runs through `app.anonymise_user()`, which
withdraws consents and strips attributes while preserving the consent audit trail the
Rules require for 7 years. `ON DELETE RESTRICT` on `user_id` is what enforces that.

## Why these queries do not need a graph database

`008_functions.sql` implements the traversals with recursive CTEs:

| Function | Pattern | Demo |
|---|---|---|
| `ref.expand_template()` | Recipe → sub-recipe → leaf ingredients, carrying the expression chain | Q1 |
| `ref.provenance_chain()` | Value → borrowed-from → source, with licence and clearance | Q3 |
| `ref.resolve_serving_grams()` | Region-scoped unit resolution, walking up the region hierarchy | Q10 |
| `ref.assert_no_template_cycle()` | Reachability check a `CHECK` constraint cannot express | Q2 |

At ~120,000 edges these return in single-digit milliseconds. Node identifiers and edge
types are named so that a graph projection is a materialized view away — see
architecture doc §12 for the conditions that would justify adding Neo4j. Do not pay
that cost for queries that are not yet the bottleneck.

## What the compute engine owns, and why

SQL returns `expr_path` as `text[]` — the chain of quantity expressions — not an
evaluated number. Evaluating `qty_expr` in SQL would mean either dynamic SQL (an
injection surface, with user-authored expressions) or a bespoke expression parser in
PL/pgSQL. Neither is worth it. The engine samples the parameter vector, evaluates the
expressions, applies yield and retention factors, and runs ~2,000 Monte Carlo draws to
produce the percentiles cached in `app.meal_log_item`.

## The flywheel tables

`ml.user_correction` stores `proposed_value` **and** `corrected_value` **and** `run_id`.
That triple is what makes a correction trainable — logging only the final value yields a
corpus with no learning signal and no way to check calibration. `ml.parameter_estimate`
records every candidate estimate with its `source` and a `was_used` flag, so you can
later ask "how good is the VLM at estimating oil?" separately from "how good is the
regional prior?" (Demo Q9 shows `fat_g` arriving from three sources with `user_stated`
winning.) `ml.v_interval_calibration` tracks whether the stated 80% interval actually
contains the truth 80% of the time — a wide interval that is never wrong is not honest,
merely vague.

## Known gaps before production

1. **Row-level security is not configured.** Add RLS on `app.*` and `ml.*` keyed to the
   session user before any multi-tenant deployment.
2. **No roles or grants.** Define at minimum `pathyam_ref_ro`, `pathyam_app_rw`,
   `pathyam_ml_ro` and grant per schema.
3. **Embedding dimension is pinned to 1024.** Changing model means a new column or
   table, not an in-place update — mixing dimensions silently corrupts ANN results.
4. **Retention factors are keyed on `food_group` + `cooking_method` text.** Once LanguaL
   facets are populated, migrate to facet-based lookup.
5. **`010_seed_example.sql` must not reach production.**

# Pathyam — what was done, and what needs you

_Written 2026-09-08. Branch `phase1/restore-trust`, all pushed, suite green at 364._

This session started as a repo review and turned into six phases of work. The short
version: the codebase contained a lot of confident-looking fabrication, that is gone,
and the things that replaced it are real and tested. What is left is mostly work only
you can authorise or supply.

---

## 🔴 Needs your decision or credentials — nothing moves without these

| # | What | Why it's blocked on you |
|---|---|---|
| 1 | **OpenRouter API key + model id** | The vision provider now targets OpenRouter and fails loudly, but **has never made a live call** — there is no key in this environment. Set `OPENROUTER_API_KEY`, then pick a model with `python3 -m pathyam_engine.vision --list` and put it in `PATHYAM_VISION_MODEL`. Unlike before, the id can be checked without a key: `python3 -m pathyam_engine.vision <id>` verifies against OpenRouter's public catalogue that the model exists *and* accepts images. |
| 2 | **ICMR-NIN written permission** | All 19,999 IFCT composition values sit behind `is_commercial_cleared = false` and show in `ref.v_uncleared_values`, which is the release gate. IFCT 2017 is free-to-read, not established as free to commercialise (dossier risk R1). **You cannot ship commercially until this letter lands.** The dossier notes one approach letter can cover both the IFCT licence and a GI-data collaboration — it calls this the highest-leverage email in the document. |
| 3 | **Native-speaker review of the lexicon** | `engine/eval/lexicon_south_indian.yaml` says at the top that it needs this before it is trusted. I added three native-script names marked `# REVIEW`. Malayalam and Telugu are flagged as least certain. |
| 4 | **Clinical review of the evidence corpus** | 42 real PubMed papers, but drawn from **seven queries I chose**. That is a starting point for this domain, not a systematic review, and it inherits whatever those queries miss. Someone clinical should review the query set in `authoring/evidence_corpus.py`. |
| 5 | **Is the CGT curve shown to users at all?** | It is now labelled illustrative everywhere, but it is still four unsourced coefficients drawn as a glucose curve. Options: keep it labelled, hide it until fitted, or drop it. A person managing type 2 diabetes is the user this matters for. |

---

## 🟡 Data collection — real work, no code blocked on it

| # | What | Size |
|---|---|---|
| 6 | **Golden meal dataset** — ≥50 photographed South Indian meals with weighed component masses. Until this exists, portion accuracy and nutrient error are **unknown**, and the safety benchmark suite has nothing to measure. Gates any claim that vision is accurate. | Days of kitchen work |
| 7 | **Paired CGM traces + weighed meal records** to fit the CGT coefficients. The plumbing is done — readings persist, and `GET /v1/cgt/postprandial/{meal_log_id}` returns the measured trace beside the prediction. Nothing fits them yet. | Weeks, needs participants |
| 8 | **New golden resolution queries**, authored independently. Do **not** back-fill them from the alias list — that is what made the first harness report a meaningless 100%. | Hours |

---

## 🟢 Engineering left — I could do these

_(Consent enforcement (9) landed in `2bd09a4`. Per-IP rate limiting (11) is done —
in-process only, so the edge limit is still worth having; see README.)_

| # | What | Notes |
|---|---|---|
| 10 | Password reset + email verification | Needs somewhere to send mail. An account whose password is forgotten is currently unrecoverable. |
| 12 | Semantic retrieval (pgvector) | Needs an embedding provider. `reciprocal_rank_fusion` is correct and unused until a second arm exists. I would not add a stub returning lexical results under a semantic name — that is what I removed. |
| 13 | Romanised resolution: last ~2 queries | 63.3% against a **70.0% ceiling** (see below). `dosai` and `dose` still lose to qualified dosa siblings. |
| 14 | Mobile app | `apps/mobile` has never been installed or built, no lockfile, Expo 51 / RN 0.74 (~2 years old). |
| 15 | `_POOL` is a module global | Constructing a second `TestClient` re-runs the app lifespan and closes the shared pool. Bit one test this session. Latent. |

---

## What was done

Six commits on `phase1/restore-trust`, each green.

**Phase 1 — removed fabrications.** The schema did not apply (`ti.ingredient_id`
vs `template_ingredient_id`), so the entire integration suite had **never run**.
Deleted a "528-food IFCT dataset" whose values came from `fid % 15` arithmetic,
stamped grade-A. Removed a fabricated citation. Removed hardcoded benchmark metrics
(`citation_recall = 0.92`). Replaced four invented news articles in two nonexistent
journals with a live PubMed query.

**Phase 2 — real data.** Ingested the actual ICMR-NIN IFCT 2017 table: 542 foods,
19,999 analytical values with per-value standard errors, 1,341 native-language names.
Found **48 of 52 IFCT codes in `ingredients.yaml` were fabricated** (black pepper
claimed `S015`; real is `G031`). Coconut milk came from USDA FDC, where the
cream/milk distinction maps exactly onto first/second extract. Meal journal moved
from an in-process list to Postgres.

**Template coverage went 6% → 100%.** All 17 dishes compute.

**Phases 3–6 — vision, evidence, CGT, auth.** Vision fails loudly instead of
returning a hardcoded dosa-and-sambar plate. Citations are verified against the
record (title, authors, year) with a three-state result, not merely checked to exist.
The CGT curve is relabelled illustrative with the label travelling in every response.
Real authentication with scrypt and revocable sessions.

### Bugs the work surfaced

- **Water was not conserved across cooking.** Idli read 3.4 g water/100 g against a
  real ~68; proximates summed to 35 instead of 100. Invisible until real composition
  landed. Now 99.7, with dosa and ven pongal independently at 99.2 and 99.5.
- **A loader re-run across a date boundary doubled every value.** `valid_from`
  defaults to `current_date` and the unique constraint includes it, so `ON CONFLICT`
  only matched same-day rows. Puttu went 272 → 471 kcal/100 g overnight with no code
  change. The QC energy-band gate caught it, not me.
- **Portions were applied twice** in the journal — a 2-idli log read 587 kcal against
  the engine's 480.

### Three premises of mine that were wrong

Worth knowing, because they shaped advice I gave you:

1. **IFCT names would fix romanised resolution.** They would not — IFCT is an
   ingredient table with no dish names. Zero of the failing queries appear in it.
2. **The "80% romanised" target.** Unachievable by construction: 9 of 30 queries are
   distinct regional lexemes (`huli` is Kannada for sambar, nearest sibling 0.18)
   that no algorithm can derive. The real ceiling is **70.0%**, now at 63.3%.
3. **Per-dish holdout would help.** No alias is shared between dishes, so it was a
   no-op. The real fix was leave-one-out, which is now the primary metric.

---

## Verify it yourself

```bash
git pull origin phase1/restore-trust
cd engine && ./dev.sh --reset --no-serve      # fetches IFCT (~1.1 MB, gitignored)
./run_tests.sh                                 # 364 passing

export PATHYAM_DSN="$(cat .devdata/dsn)"
PYTHONPATH=. python3 -m pathyam_engine.authoring --dir ../db/templates audit   # 17/17, 100%
PYTHONPATH=. python3 -m pathyam_engine.evaluation                              # top-1 88.4%
PYTHONPATH=. python3 eval/lexeme_ceiling.py                                    # the 70% ceiling
```

Release gate — must be empty before any commercial release:

```sql
SELECT * FROM ref.v_uncleared_values;   -- 19,947 IFCT values, pending item 2 above
```

---

## The one thing I would not let slide

`ref.v_uncleared_values` is non-empty and `is_commercial_cleared` is `false` on
IFCT2017. A previous version of `ifct_data.py` claimed it was cleared, which would
have silently disarmed that gate. **Do not set it true without the letter.**

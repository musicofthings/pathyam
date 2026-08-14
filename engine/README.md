# Pathyam Engine, Resolution and API

Three layers: free text → dish identity → nutrients with credible intervals.

**Status:** **255 tests passing** — 196 unit, 59 integration and API against a live PostgreSQL instance seeded from `../db`. Python 3.10+, NumPy for engine (plus `psycopg` for repository and `fastapi` for service).

For full project architecture, accomplished components, pending roadmap, and conflict audit, see the root [`README.md`](file:///Users/theranosis_dx/projects/pathyam/README.md).

```bash
./run_tests.sh            # unit + integration + API (spins up a throwaway Postgres)
./run_tests.sh --unit     # unit only, no database
```

| Layer | Package | Question it answers | Nature |
|---|---|---|---|
| Resolution | `pathyam_engine.resolution` | What did the user eat? | Probabilistic — returns candidates and scores |
| Computation | `pathyam_engine` | What is in it? | Deterministic — returns intervals and provenance |
| Service | `pathyam_api` | Both, over HTTP | `/v1/resolve`, `/v1/compute`, `/v1/log` |

The boundary is load-bearing: `/v1/resolve` cannot return a nutrient value and
`/v1/compute` cannot guess a dish, so no response can contain a number that did not
come out of the engine's arithmetic. `test_resolve_returns_no_nutrient_values` asserts
this against the serialised response.

```
>>> "2 masale dose, swalpa enne"   [kn/KA]
    parsed  qty=2.0 dish='masale dose' hints={'fat_g': 0.6}
    match   Dosa, masala  conf=0.99 margin=0.51 via=phonetic
    RESULT  Dosa, masala: 248 kcal (191-323, 80% CI)  | largest uncertainty: batter_g
            fat_g prior p50 = 6.06 g (supplied_prior)
```

Note what "swalpa enne" did: it **scaled the oil prior by 0.6**, from 9.97 g to 6.06 g,
and the result stayed a distribution. The user said *less oil*, not *exactly 6.06 g*,
so pinning a number would have manufactured precision they never supplied.

## What it does, and what it deliberately does not

```python
from pathyam_engine import ComputeEngine, PostgresRepository
import psycopg

with psycopg.connect(DSN) as conn:
    engine = ComputeEngine(PostgresRepository(conn))
    r = engine.compute("PY-T-000101", region_key="KA",
                       param_overrides={"fat_g": 11.0, "fat_type": "ghee"})
    print(r.summary_line())
    # Dosa, masala: 538 kcal (432-681, 80% CI)  |  largest uncertainty: batter_g
```

No part of this package calls a language model. Perception and entity resolution
happen upstream and hand the engine a template plus parameter estimates. The engine's
contract is that **identical inputs produce byte-identical output** — which is why
`test_determinism_against_the_database` asserts full `as_dict()` equality, and why the
seed is derived with blake2b rather than Python's per-process-salted `hash()`.

## Five things worth knowing before reading the code

**1. `expressions.py` is a security boundary, not a utility.**
`qty_expr` comes from a database table that nutritionists edit. `eval()` there would
hand anyone with write access to `ref.template_ingredient` code execution inside the
compute service. The evaluator walks the AST and permits only an explicit whitelist;
attribute access, subscripts, lambdas, comprehensions, imports and walrus are rejected
*before* evaluation, and nothing is ever passed to `eval` or `exec`. Fourteen attack
strings are asserted-rejected in `test_expressions.py`. Adding to the whitelist is a
security decision.

**2. A sub-recipe reference is a target mass, not a multiplier.**
`filling_g` means "70 g of potato masala", so the engine computes the sub-template's
own batch mass from its own sampled parameters and rescales. This is why the engine
recurses rather than consuming `ref.expand_template()`'s flattened expression chain —
the normalisation needs the sub-batch total, which is not a product of the chain.
`test_sub_recipe_target_mass_is_normalised` pins it: a 50 g batch asked for 100 g
yields 80 g potato and 20 g onion, not 100 g of each.

**3. Truncation resamples; it does not clip.**
Mass parameters get an implicit `lo=0`. Clipping a normal at zero piles probability
mass on the bound and biases the mean upward. `_draw_within_bounds` resamples the
violating draws instead, and only clips after 24 rounds — recording a warning when it
does, because needing the fallback means the prior is mis-specified for its bounds.

**4. Composition uncertainty propagates alongside parameter uncertainty.**
Where a source reports an analytical SD, the engine samples the composition value too.
`test_composition_sd_widens_the_interval` shows the difference. Turn it off with
`sample_composition_sd=False` when you want to isolate parameter effects.

**5. Every result carries its sources and flags uncleared ones.**
`result.uncleared_sources` is non-empty whenever a value rests on a source without
commercial clearance — currently IFCT2017, until that permission lands. The engine
warns rather than refusing, so development is not blocked, but the flag is in every
response and in the CLI output.

## A finding that corrects the architecture document

`PATHYAM_AI_ARCHITECTURE.md` §7 asserts that the parameter dominating energy variance
"is almost always cooking oil". **Running the sensitivity analysis on the seeded
templates shows that is wrong.** For a plain dosa with the seeded priors:

```
batter_g        76.2%   ##############################
fat_g           18.2%   #######
fat_type         5.6%   ##
rice_type        0.1%
```

Portion size dominates, by roughly 4:1 over cooking fat. In hindsight this is obvious —
batter is ~90 g of the ~100 g total and its prior spans roughly ±28% at one SD, while
fat is ~8 g however uncertain. **The practical consequence is that the elicitation
question should ask about size before oil**, which is the opposite of what the seed
data encodes (only `fat_g` carries an `elicitation_question`). Worth fixing in the
templates, and worth re-checking per dish family — deep-fried items like vada will
genuinely be oil-dominated, because oil absorption there is both large and variable.

This is the sensitivity machinery doing its job: contradicting a plausible assumption
with a number.

## Four bugs the tests and the demo caught

Worth recording, because three of them were silent — they produced plausible output
rather than an error.

**1. Python and SQL disagreed on Indic normalisation.** The obvious implementation,
`re.sub(r'[^\w]+', ' ', text)`, turns `தோசை` into `த ச`: Python's `\w` excludes
combining marks, and Tamil vowel signs *are* marks (category Mc). PostgreSQL's
`[[:alnum:]]` keeps them. So `ref.normalize_name()` and the Python mirror silently
diverged on exactly the four scripts this product exists for, and the native-script
half of the lexicon would have stopped matching with nothing in the logs. Fixed
Python-side; the schema was correct.

**2. Trigram similarity alone cannot bridge South Indian romanisation.**
`similarity("dosa", "thosai")` is **0.09** — far below any usable threshold. Since the
same dish gets written dosa/dosai/dhosa/thosai/dose/dosey depending on who is typing,
`phonetic_key()` folds digraphs (`th→t`, `zh→l`), voiced/unvoiced stop pairs
(`t↔d`, `k↔g`, `p↔b`), doubled consonants and trailing vowels. Phonetic matches are
discounted 0.90 and rank below direct hits, so a true match always wins.

**3. Boost capping manufactured ambiguity.** Typing the exact Tamil name for plain dosa
gave `confidence 1.00, margin 0.00` and a confirmation prompt — because masala dosa
contains that word, and its boosts hit the same 1.0 ceiling. Non-exact matches now cap
at 0.99, and a single exact match never asks for confirmation.

**4. Splitting on punctuation stranded parameter hints.** `"2 masale dose, swalpa enne"`
parsed into two items, the second holding `{fat_g: 0.6}` with no dish — so the hint was
discarded and the app then asked about oil the user had already told it about. Trailing
modifier-only chunks now merge into the preceding dish.

## Layout

| Module | Responsibility |
|---|---|
| `expressions.py` | AST-whitelist evaluator, vectorised over NumPy arrays |
| `distributions.py` | Prior sampling with rejection-based truncation; `Prior.scaled()` |
| `models.py` | Schema-mirroring dataclasses and result types |
| `repository.py` | `PostgresRepository` and `InMemoryRepository` behind one interface |
| `engine.py` | Recursive template walk, Monte Carlo, sensitivity attribution |
| `qc.py` | Nine FAO/INFOODS-derived consistency gates |
| `cli.py` | `python -m pathyam_engine compute PY-T-000101 --region KA` |
| `resolution/trigram.py` | pg_trgm-compatible similarity + Indic phonetic folding |
| `resolution/text_parser.py` | Quantity, unit, dish and modifier extraction |
| `resolution/sources.py` | Candidate lookup; degrades gracefully without `pg_trgm` |
| `resolution/resolver.py` | Reranking, deduplication, confirmation thresholds |
| `../pathyam_api/` | FastAPI service and Pydantic contract |

## Resolution notes

**Number words in four languages, including colloquial forms.** `rendu` not just
`irandu`, `moonu` not just `moondru` — real logs use the spoken form. Native script,
romanised, digits and fractions all parse.

**Modifiers become priors, not pinned values.** `konjam`/`swalpa`/`korachu` → 0.6×,
`romba`/`jaasti`/`extra` → 1.5×. When a modifier precedes an ingredient word
(`konjam ennai`) it becomes a `fat_g` hint; when it stands alone it scales the portion.
A bare ingredient word stays in the dish phrase, so *ghee roast* is a dish rather than
a comment about fat.

**The Postgres source degrades rather than failing when `pg_trgm` is absent.** Because
`trigram.py` reimplements pg_trgm's scoring faithfully, the fallback returns the same
ranking — slower, not different. That also means an offline eval harness built on this
module says something true about the deployed system.

**Confirmation thresholds.** `confidence < 0.72` or `margin < 0.08` means ask. Margin
matters independently: a bare "dosa" against a lexicon holding *set dosa* and *ghee
dosa* is high-confidence and genuinely ambiguous, and those differ by ~100 kcal.
`/v1/log` withholds computation for such items unless `auto_accept` is set — silent
guessing costs both user trust and the correction signal the fine-tuning corpus needs.

## Sensitivity attribution

Continuous parameters: squared Spearman rank correlation with the output.
Categorical parameters: eta-squared (between-group variance share). Both implemented
in ~20 lines, which is why scipy is not a dependency.

This is **first-order** — it ignores interactions, so shares are indicative rather than
a strict variance decomposition. That is enough for its actual job: choosing the single
question whose answer collapses the most uncertainty. If interactions later matter,
Sobol indices via `SALib` would be the upgrade, at roughly `n_params × n_samples` extra
evaluations.

## QC gates

Nine gates run on every compute and never raise — a broken gate degrades to `FAIL`
rather than taking down the request. `PASS` / `WARN` / `FAIL` / `SKIP`, where `SKIP`
means the nutrients the gate needs are not in the dataset yet.

`proximate_sum` currently reports `SKIP` because the seed lacks WATER and ASH; it
activates automatically once real IFCT data loads. `energy_atwater` is the most useful
gate in practice — on seeded data it reconciles to within 1.2%, so a future drift would
be a real signal.

## Running the API

```bash
export PATHYAM_DSN='postgresql://...'
uvicorn pathyam_api.main:app --reload
# OpenAPI at /docs
```

| Endpoint | Purpose |
|---|---|
| `POST /v1/resolve` | Text → ranked candidates. No nutrient values, by contract. |
| `POST /v1/compute` | Template + parameters → intervals, provenance, QC. |
| `POST /v1/log` | Both, in one call. The primary app path. |
| `GET /v1/health` | Catalogue size, `pg_trgm` presence, uncleared-source warning. |

## Known gaps

1. **No `WATER`/`ASH` in the seed**, so the strongest consistency gate is dormant.
2. **Retention factors are keyed on `(food_group, cooking_method, nutrient)` text.**
   Missing combinations default to 100% retention with a warning naming the specific
   nutrients — heat-labile vitamins are overstated where that applies.
3. **First-order sensitivity only** (see above).
4. **No caching.** Every call recomputes; add a cache keyed on
   `(template_id, binding_hash, prior_version, engine_version)` before serving traffic.
5. **No vector retrieval yet.** `ref.food_embedding` and the HNSW index exist in the
   schema, and `RawCandidate.method` reserves `"vector"`, but nothing populates or
   queries them. Trigram plus phonetic folding covers typed input well; vector search
   earns its place for semantic queries ("that rice thing with curd") and for reranking
   VLM output in Phase 3.
6. **No authentication, rate limiting or request logging** on the API. It is not
   deployable as-is.
7. **Energy summing across items assumes independence.** Two dishes photographed on one
   plate share a portion-size bias, so quadrature understates the combined interval
   slightly. Correct fix is to sample jointly rather than to sum summaries.
8. **`InMemoryRepository` reaches into private attributes in two tests**
   (`repo._composition`, `repo._by_key`) to inject edge cases. Fine for tests, but if
   that pattern spreads, give the class a proper mutation API.
9. **The seed lexicon has no idli**, so `"3 idli"` correctly resolves to nothing. That
   is right behaviour on missing data, but it makes the demo look thinner than the
   system is.

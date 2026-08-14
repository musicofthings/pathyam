# Pathyam — AI-Native Architecture

**Data stores, retrieval design, perception pipeline, uncertainty engine, and clinical validation**

12 August 2026 · Companion to `PATHYAM_RESEARCH_DOSSIER.md` and `PATHYAM_SOURCE_COMPARISON.xlsx`

---

## 0. The recommendation, up front

1. **Postgres as system of record.** Not Neo4j, not MongoDB — yet. At Pathyam's scale (~5k dishes, ~100k edges) recursive CTEs beat a graph database on every axis that matters, and you avoid running two datastores with a two-person team. §3 shows the numbers and §12 names the exact conditions under which I'd change this answer.
2. **Do not let RAG touch a nutrient number.** Retrieval resolves *identity*; deterministic arithmetic computes *values*. An LLM that emits calorie figures gives you non-reproducible output, no audit trail, no error bounds, and no clinical defensibility. §2.
3. **Model recipes parametrically, not as flat rows.** ~400 templates with typed parameters, not 5,000 enumerated recipes. This is the single most important design decision in the document — it makes personalization, uncertainty and VLM inference all fall out naturally instead of being bolted on. §5.
4. **Ship uncertainty intervals, not point estimates.** Peer-reviewed state of the art is **23% MAPE on food volume alone** (MetaFood CVPR 2025 winner). End-to-end photo→calorie is realistically 20–40%. Reporting "385 kcal" is a lie; reporting "385 kcal (290–520)" plus the dominant error term is the product. §7.
5. **Align to FoodOn / LanguaL / FoodEx2 / INFOODS from day one.** Cheap now, a rewrite later, and it is what makes Pathyam interoperable and citable rather than another silo. §4.
6. **The clinical evidence is a prospective validation study, not a marketing page.** ~200 meals against weighed food records, pre-registered on CTRI, published. §9. Read §13 first — the market you are entering is full of accuracy claims that do not survive contact with the literature.

---

## 1. Framing: one app, three engines

The commonest architectural failure in this category is treating "AI-native" as "put an LLM in the middle." Pathyam answers three different questions, and they have different correctness criteria:

| Question | Nature | Engine | Correctness criterion |
|---|---|---|---|
| **What did the user eat?** | Perceptual, probabilistic, ambiguous | VLM + retrieval + reranking over your ontology | Calibrated posterior over dish + parameters |
| **What is in it?** | Arithmetic, deterministic | Composition engine over Postgres | Bit-identical reproducibility; auditable to source |
| **What does it mean for this patient?** | Interpretive, evidence-bound | RAG over a cited literature corpus + rules | Every claim traceable to a PMID/DOI or a guideline clause |

Keeping these separate is what makes the system defensible. Collapsing them into one LLM call is what makes a wrapper.

```
    ┌──────────────┐   probabilistic   ┌────────────────┐   deterministic   ┌──────────────┐
    │  PERCEPTION  │ ────────────────► │  COMPUTATION   │ ────────────────► │  EXPLANATION │
    │  VLM + RAG   │  dish + params    │  recipe engine │  nutrients + CI   │  RAG + rules │
    │              │  + uncertainty    │  Monte Carlo   │                   │  + citations │
    └──────────────┘                   └────────────────┘                   └──────────────┘
      may be wrong;                     never guesses;                       never asserts
      says how wrong                    always reproducible                  without a source
```

---

## 2. Why RAG must not produce the numbers

You framed this as "RAG to retrieve nutrition programmatically." I want to push back on that specific framing, because it is the one decision that would be hardest to undo.

Classic RAG retrieves text chunks, stuffs them into context, and lets the model generate an answer. Applied to nutrient values, that gives you:

- **Non-reproducibility.** The same idli logged twice returns different numbers. For a wellness app that is embarrassing; for a CKD potassium calculation it is dangerous, and for a regulator it is indefensible — a "calculation" whose output varies between runs is not a calculation.
- **No audit trail.** You cannot answer "where did 312 kcal come from?" That destroys the provenance moat which is the entire strategic thesis of this project.
- **No error bounds.** An LLM's confident tone is uncorrelated with its numerical accuracy.
- **Unfixable failure modes.** When a value is wrong you cannot patch it — you can only re-prompt and hope.
- **Arithmetic drift.** LLMs are unreliable at multi-step arithmetic over many operands. A thali has 12 components, each needing quantity × per-100g × retention.

**The correct pattern is tool-use over a deterministic engine.** The LLM decides *what to look up and what to call*; a SQL query and a Python function produce every number. The model never emits a figure it did not receive from the engine. This is strictly more agentic than RAG, not less.

**Where genuine RAG belongs — and it does belong:**

| Layer | Retrieval type | What it returns |
|---|---|---|
| Dish resolution | Hybrid: pgvector embeddings + `pg_trgm` trigram + lexicon | Candidate `dish_id`s with scores — never nutrients |
| Ingredient resolution | Same, over `food_item` + FoodOn synonyms | Candidate `food_id`s |
| **Evidence / clinical Q&A** | **True RAG over a document corpus** | **Passages + PMID/DOI citations** |
| Guideline lookup | RAG over DGI 2024, RDA 2020, FSSAI regs | Clause text + citation |

The evidence layer is where "AI-native with clinical evidence" actually lives: *"Why is vada sambar lower GI than plain dosa?"* → retrieve Shakappa 2022 → answer with the citation attached. That is RAG doing what RAG is good at.

---

## 3. Store selection: the honest comparison

### 3.1 The actual query patterns

| # | Pattern | Frequency | Shape |
|---|---|---|---|
| Q1 | Dish → sub-recipe → ingredient expansion | Every meal log | Recursive, depth ≤ 4 |
| Q2 | Fuzzy name in ta/te/ml/kn (misspelled, code-mixed) → canonical dish | Every manual log | Vector + trigram |
| Q3 | Nutrient value → provenance chain → source citation | Every clinical view | Recursive, depth ≤ 3 |
| Q4 | Constraint filter ("< 15 g carb AND K < 200 mg AND region = Kerala") | Clinical tier | Relational aggregate + index scan |
| Q5 | Ingredient substitution ("no coconut — what changes?") | Occasional | Graph-ish, 1–2 hops |
| Q6 | Evidence Q&A over literature | Occasional | Vector over text |

### 3.2 The scale, which is the decisive fact

```
dishes (templates + instances)   ~5,000
ingredients                      ~2,500
nutrients                          ~150
composition_value             ~375,000 rows
recipe_ingredient edges        ~40,000
provenance edges               ~30,000
lexicon entries (4 langs × syn)~50,000
────────────────────────────────────────
total graph edges              ~120,000
```

**This is a small dataset.** It fits in RAM on a laptop. Recursive CTEs over 120k edges at depth 4 return in single-digit milliseconds on Postgres with correct indexes. Graph databases start to earn their keep somewhere around 10⁷–10⁸ edges, or when graph traversal *is* the primary workload. Here the primary workload is "expand a recipe and multiply" — arithmetic over a shallow tree.

### 3.3 Option comparison

| | **Postgres** | **Neo4j (graph)** | **MongoDB** |
|---|---|---|---|
| Q1 recipe expansion | Recursive CTE, ~2 ms | Native, ~1 ms | Manual `$graphLookup`, awkward |
| Q2 fuzzy multilingual | `pgvector` + `pg_trgm` in-engine | Needs a separate vector store | Atlas Search — decent |
| Q3 provenance chain | Recursive CTE | Native, elegant | Application-managed |
| Q4 constraint filter | Native, indexed, fast | Weak — not its job | Aggregation pipeline, workable |
| Q5 substitution | Recursive CTE | **Native, genuinely better** | Poor |
| Q6 evidence RAG | `pgvector` | Separate store | Atlas Vector Search |
| Numeric integrity / ACID | **Strong** | Adequate | Weak by default |
| Referential integrity of provenance | **Enforced by the DB** | Enforced by convention | **Your application's problem** |
| LLM-to-database reliability | **text-to-SQL, best-supported path** | text-to-Cypher, far less training data | text-to-MQL, poor |
| Ops burden for a 2-person team | **One engine** | Second engine, second language | Second engine |
| Scientific-computing ecosystem | pandas/polars/dbt/DuckDB all native | Weaker | Weaker |

### 3.4 Verdict

**Postgres 16+ as system of record**, with:

- `pgvector` — dish and ingredient embeddings for Q2/Q6
- `pg_trgm` — trigram fuzzy match for misspellings and transliteration variants
- `ltree` *or* recursive CTEs — recipe and provenance hierarchies
- `JSONB` — parameter bindings and prior distributions, where the schema is genuinely variable
- **Materialized views** → a denormalized read model (one row per dish with everything precomputed)

**On MongoDB specifically:** it is a reasonable *read model* — a precomputed dish document (`{dish, all params, nutrient vector, serving units, lexicon, citations}`) served at single-digit ms is exactly what the app needs, and if your team is more fluent in Mongo than in Redis, use it there. It is the wrong choice for the *system of record*, because the entire strategic value of Pathyam-DB is enforced referential integrity between a value, its source, its method and its confidence tier. Mongo makes that the application's responsibility, which means it will eventually be violated.

**On the graph:** design the relational schema so a graph projection is a materialized view away (stable `node_id` / `edge_type` naming). Then adding Neo4j later is an export job, not a migration. §12 lists what would trigger it.

---

## 4. Ontology alignment — do not invent your own

Map every entity to established vocabularies from the first commit. This is a few weeks of work now and a rewrite in year two.

| Vocabulary | What it is | Use in Pathyam |
|---|---|---|
| **INFOODS tagnames** | FAO nutrient identifiers | `nutrient.infoods_tagname` — mandatory, already in the dossier schema |
| **FoodOn** | Farm-to-fork OWL ontology; **9,600+ generic food product categories**, largely derived from LanguaL | `food_item.foodon_iri` — the interoperability backbone |
| **LanguaL** | Descriptive food indexing, **14 facets** (source organism, preservation, cooking method, packaging, consumer group…) | Facet-tag every dish: cooking method, fat used, fermentation state. Makes retention-factor lookup a join rather than a guess |
| **FoodEx2** | EFSA food classification and description | Needed if you ever export to EU contexts |
| **FoodOntoMap** | Cross-ontology concept linking | Reconcile FoodOn ↔ LanguaL ↔ FoodEx2 without doing it by hand |

**Where South Indian food will break these ontologies — and why that is an opportunity.** FoodOn has no concept for *idli batter, naturally fermented 12 h*, no *Kerala Sadya* meal structure, no distinction between first/second/third coconut-milk extract. Extending FoodOn with a South Indian branch and contributing it upstream is a low-cost, high-credibility research output — the consortium accepts contributions, and it makes Pathyam the reference implementation for the region.

**Entity linking:** FoodSEM (arXiv 2509.22125) is an LLM specialised for food named-entity linking — evaluate it for the ingredient-resolution layer before writing a custom matcher.

---

## 5. The parametric recipe model — the core design decision

### 5.1 The problem with enumerated recipes

Dosa is not a recipe. It is a family. Enumerate the real variation and you get combinatorial explosion:

```
4 states × 6 preparation styles × 3 cooking fats × 2 rice types × {filled, plain} = 288 rows
… for one dish. Across ~400 dish families → ~115,000 rows, each needing manual curation.
```

That is unbuildable, and it still would not cover *how much oil your mother-in-law uses*.

### 5.2 The model

Store **recipe templates with typed, distributed parameters**. A specific dish is a *parameter binding*, not a row.

```sql
recipe_template (
  template_id, canonical_dish_id, base_method, langual_facets[], notes
)

template_parameter (
  template_id, param_name, dtype, unit,
  default_value,
  prior_dist,            -- 'lognormal' | 'normal' | 'categorical' | 'point'
  prior_params JSONB,    -- {mu, sigma} | {categories, weights}
  observable_from_image  -- BOOLEAN: can a VLM see this?
)

template_ingredient (
  template_id, food_id,
  qty_expr               -- safe expression referencing params, e.g. '0.35 * batter_g'
)

dish_instance (
  instance_id, template_id, param_bindings JSONB, region_id, provenance
)

user_parameter_prior (
  user_id, template_id, param_name,
  dist, params JSONB, n_observations, updated_at
)
```

Example — the dosa template:

| Parameter | Type | Prior | Observable from image? |
|---|---|---|---|
| `batter_g` | continuous | lognormal(μ=ln 90, σ=0.25) | **partially** — via size/area |
| `fat_type` | categorical | {gingelly .45, coconut .30, ghee .15, sunflower .10} — region-conditioned | rarely |
| `fat_g` | continuous | lognormal(μ=ln 8, σ=0.55) | **no** |
| `rice_type` | categorical | {parboiled .7, raw .3} — region-conditioned | no |
| `fermentation_h` | continuous | normal(12, 3) | no |
| `filling_g` | continuous | 0 if plain; lognormal(μ=ln 70, σ=0.3) | **yes** |

### 5.3 Why this is the decision that makes everything else work

- **Curation collapses.** ~400 templates instead of ~115,000 rows. A nutritionist authors a template in an afternoon; `qty_expr` is stored as data, so no engineering is needed to add a dish.
- **The VLM's job becomes tractable.** It stops being a 5,000-class classifier — a task the 2025 literature says it fails at — and becomes a parameter estimator over a small named space. Predicting "dosa template, size≈large, filling present" is a problem VLMs are actually good at.
- **Uncertainty is native, not bolted on.** Every parameter already carries a distribution. Propagating it is a Monte Carlo pass, not a retrofit.
- **Personalisation is native.** `user_parameter_prior` updates by conjugate Bayesian update every time a user corrects a value. After ~15 dosas the app knows this household's oil habit, and the interval tightens. **This is the moat.** A competitor can copy your dish list; they cannot copy six months of a user's learned priors.
- **Regional variants are parameter priors, not new rows.** "Kerala" shifts `fat_type` toward coconut and `rice_type` toward parboiled. One line of data, not 288 rows.

---

## 6. Perception pipeline

**Do not train a food classifier.** The published benchmarks say it will not generalise across the variation you care about — arXiv 2504.06925 found that VLMs (and by extension classifiers) degrade sharply on fine-grained *cooking-style* discrimination, which is exactly the axis that separates plain dosa from ghee roast.

```
 photo
   │
   ├─► [1] VLM → structured JSON, not free text
   │       { dish_candidates:[{name, confidence}],
   │         visible_ingredients:[...], vessel:'steel plate',
   │         count:3, attributes:{browning:'high', oil_sheen:'high'},
   │         reference_objects:['spoon','tumbler'] }
   │
   ├─► [2] Embed candidates → pgvector ANN over dish ontology → top-k templates
   │       (this step is where your proprietary 4-language lexicon does the work)
   │
   ├─► [3] Cross-encoder rerank → posterior P(template | image, user history, time of day, region)
   │
   ├─► [4] Volume/portion:
   │        geometry module where a reference object is present (MonoBite-class), else
   │        vessel-anchored prior (katori/plate/leaf → ml → g via density)
   │
   ├─► [5] Unobservable parameters ← user prior ▸ regional prior ▸ population prior
   │
   └─► [6] Deterministic engine + Monte Carlo → nutrients with credible intervals
```

**Step 2 is the defensible part.** Anyone can call a VLM. Retrieval against a curated South Indian dish ontology with four-language synonyms, regional priors and per-template parameters is the asset. The model is a commodity; the index is not.

**Design rules:**

- The VLM returns **structured output only** (JSON schema / constrained decoding). Never free prose that you then parse.
- The VLM **never sees nutrient values** and never emits them. It identifies; it does not compute.
- **Always user-confirmed.** Present the top candidate with its interval and a one-tap correction. Silent logging destroys both trust and your training signal.
- **Ask one question, not five.** Target the parameter with the highest variance contribution, measured rather than assumed. > **Correction (12 Aug 2026, from running the built engine):** this section originally claimed the dominant parameter "is almost always cooking oil". Measured on the seeded templates, that is wrong for griddled items — plain dosa attributes **76% of energy variance to `batter_g` (portion size) and only 18% to `fat_g`**. Batter is ~90 g of a ~100 g dish; fat is ~8 g however uncertain. So **ask about size first, oil second**, and re-measure per dish family: deep-fried items like vada should genuinely be oil-dominated, since absorption there is both large and variable. See `engine/README.md`.
- Every correction writes to `user_parameter_prior` **and** to the eval set.

---

## 7. The uncertainty engine — this is the product, not a caveat

### 7.1 What the literature actually supports

| Result | Source |
|---|---|
| **23% MAPE on food volume alone** — winning entry, CVPR 2025 MetaFood challenge (MonoBite) | Challenge results |
| Geometry-based reconstruction **beats VLM baselines** for volume | MetaFood 2025 benchmark |
| VLMs >90% expert-weighted recall on *single-product* images; **fail on fine-grained cooking style** | arXiv 2504.06925 |
| Weighed food records themselves under-report energy by **11–41%** vs doubly labelled water | DLW systematic reviews |

Volume is the *easier* half of the problem. Stack ingredient-identification error, oil variance (a genuine 2–5× range in home cooking), cooking-method effects and density assumptions on top, and honest end-to-end photo→calorie error is **20–40%**.

### 7.2 What Pathyam should therefore ship

```
  Masala dosa · Karnataka style
  ────────────────────────────────────────────────
  385 kcal        (290 – 520, 80% CI)
  Carbohydrate    52 g   (44 – 61)
  Fat             12 g   (6 – 22)
  ────────────────────────────────────────────────
  Largest uncertainty: cooking oil  (±90 kcal)
  → Tell us how much oil and we'll narrow this. [little｜medium｜generous]

  Sources: IFCT 2017 (rice, parboiled; urad dal) · INDB recipe method
           · retention factors USDA R6 · GI 76 (Shakappa 2022, PMID 35875218)
```

Why this wins:

- **It is true.** Every competitor's point estimate is false precision, and every dietitian knows it.
- **It creates the learning loop.** The uncertainty display motivates the correction that trains the personal prior.
- **It is clinically usable.** A ±20 g carbohydrate range is actionable information for insulin dosing; "52 g" presented as fact is not.
- **It is auditable.** When a clinician challenges a number you can decompose it into parameter contributions.

### 7.3 Implementation

Monte Carlo, 2,000 samples per dish: sample parameters from priors → evaluate `qty_expr` per ingredient → look up composition → apply yield and retention factors → sum → percentiles. Vectorised with NumPy this is ~3 ms per dish. Cache the resulting distribution keyed by `(template_id, binding_hash, user_prior_version)`.

**Calibration is a first-class metric.** If your stated 80% interval contains the weighed truth only 55% of the time, you are not honest, merely vague. Track coverage in the eval harness (§9) and recalibrate priors against it.

---

## 8. The evidence layer — where real RAG lives

A separate corpus, separate index, separate rules from the composition engine.

**Contents:** your extracted GI/GL table with PMIDs · IFCT method notes · DGI 2024 · RDA 2020 · FSSAI regulations · the South Indian nutrition literature · your own methods paper once published.

**Rules:**

1. Every clinical or evidential statement carries an inline citation resolvable to a PMID, DOI or guideline clause.
2. **Abstain outside the corpus.** "I don't have evidence on that" is a correct answer and should be tuned for, not against.
3. Never restate a nutrient number from a retrieved passage — always re-query the engine so displayed values cannot diverge from computed ones.
4. Distinguish **evidence** ("Shakappa 2022 measured GI 79.7 for onion dosa") from **guidance** ("ICMR DGI 2024 recommends…") from **inference** ("for your logged intake this suggests…") with different visual treatment. Conflating these is how a wellness app drifts into making medical claims — see the dossier's §7 claims boundary.

---

## 9. Evaluation and clinical validation

### 9.1 Eval harness — clinically meaningful metrics, not "accuracy"

| Component | Metric | Target v1 |
|---|---|---|
| Dish resolution (text, per language) | top-1 / top-5 | 80% / 95% |
| Dish resolution (image) | top-1 / top-5 | 65% / 90% |
| Energy | MAPE vs weighed reference | < 25% per meal |
| **Carbohydrate** | **MAE in grams** | **< 12 g per meal** |
| Potassium (CKD tier) | MAE in mg | < 250 mg |
| **Interval calibration** | **80% CI empirical coverage** | **75–85%** |
| Evidence layer | citation accuracy; abstention rate on out-of-corpus | > 98%; > 90% |
| Recipe engine | reproduction of INDB's 1,014 recipes | within rounding |

Carbohydrate MAE is deliberately called out: it is the number a person with type 2 diabetes acts on, and it is the metric that makes Pathyam clinically meaningful rather than merely accurate on average.

### 9.2 The validation study — the actual "clinical evidence"

- **Design:** cross-sectional, ~200 meals, 4 states, home-cooked and restaurant, weighed food record as reference.
- **Comparators:** HealthifyMe, MyFitnessPal, Cronometer, manual dietitian estimate.
- **Primary endpoint:** energy MAPE. **Secondary:** carbohydrate MAE, interval coverage, per-dish-family error.
- **Pre-register on CTRI** before data collection. Publish regardless of result.
- Pair with the Phase 1 dataset methods paper.

Two published papers — a methods paper for the dataset and a validation paper for the app — is a position no competitor in this market currently holds, and it is what converts "AI-native" from a claim into a fact.

---

## 10. Reference architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CLIENTS   consumer app (ta/te/ml/kn)   ·   clinician web tool          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │  FastAPI + Pydantic (OpenAPI = B2B contract)
┌───────────────────────────────▼─────────────────────────────────────────┐
│  ORCHESTRATION — agent loop with tool-calling, NOT free generation      │
│  tools: resolve_dish · estimate_params · compute_nutrition ·            │
│         query_evidence · check_constraints                              │
└──┬───────────────┬────────────────┬───────────────┬────────────────────┘
   │               │                │               │
┌──▼─────────┐ ┌───▼──────────┐ ┌───▼───────────┐ ┌─▼──────────────────┐
│ PERCEPTION │ │  RESOLUTION  │ │  COMPUTATION  │ │  EVIDENCE          │
│ VLM (JSON) │ │ pgvector +   │ │ recipe engine │ │ RAG over cited     │
│ geometry   │ │ pg_trgm +    │ │ + Monte Carlo │ │ literature corpus  │
│ module     │ │ reranker     │ │ (NumPy)       │ │ abstains off-corpus│
└────────────┘ └──────┬───────┘ └───────┬───────┘ └─────────┬──────────┘
                      │                 │                   │
        ┌─────────────▼─────────────────▼───────────────────▼───────────┐
        │  POSTGRES 16  (system of record)                              │
        │  composition · templates · params · provenance · lexicon      │
        │  pgvector · pg_trgm · JSONB · materialized read views         │
        └───────────────────────────────┬───────────────────────────────┘
                                        │
        ┌───────────────────────────────▼───────────────────────────────┐
        │  READ MODEL — Redis or MongoDB, denormalized dish documents   │
        └───────────────────────────────────────────────────────────────┘

  ⚠ SEPARATE SERVICE, NEVER JOINED: Open Food Facts barcode lookup (ODbL)
```

**Stack**

| Layer | Choice | Note |
|---|---|---|
| System of record | PostgreSQL 16 + pgvector + pg_trgm | One engine |
| ETL / build | Nextflow or Snakemake + dbt-core + DuckDB | Matches how you already run nf-core; dbt gives column-level lineage, which *is* provenance |
| Data QC | Great Expectations / pandera | FAO checking rules as executable assertions |
| Versioning | Git + DVC, Zenodo DOI per release | Citable dataset versions |
| Compute engine | Python + NumPy, `simpleeval`/`asteval` for `qty_expr` | **Never `eval()`** |
| API | FastAPI + Pydantic | Schema is the B2B contract |
| VLM | Closed (Claude/Gemini) for accuracy now; Qwen2.5-VL for on-device later | Swappable behind one interface |
| Embeddings | Multilingual model with Indic coverage; evaluate FoodSEM for entity linking | Benchmark on your own lexicon |
| Transliteration | AI4Bharat IndicXlit (`ai4bharat-transliteration`) | Pin the version |
| Eval | Custom harness + golden set, run in CI | Blocks merges on regression |

---

## 11. Phased build

| Phase | Weeks | Deliverable | Gate |
|---|---|---|---|
| **0** | 1–6 | Licences (§dossier). Postgres schema with INFOODS/FoodOn IDs. Port `INDB.do` to Python. QC gates in CI. | INDB's 1,014 recipes reproduced within rounding |
| **1** | 6–16 | ~400 parametric templates for South Indian dishes. 4-language lexicon. GI table with PMIDs. Deterministic engine + Monte Carlo. **No AI yet.** | Engine returns calibrated intervals for 400 dishes |
| **2** | 16–24 | Resolution layer: pgvector + trigram + reranker. Text logging in 4 languages. Eval harness with golden set. | top-1 ≥ 80% on text resolution |
| **3** | 24–36 | Perception: VLM → structured JSON → retrieval → parameters. Vessel-anchored volume. User-prior learning loop. | Image top-1 ≥ 65%; 80% CI coverage 75–85% |
| **4** | 36–48 | Evidence RAG layer. Clinician tool. **Validation study** runs in parallel. | Two manuscripts submitted |
| **5** | 48+ | Consumer launch. B2B API licensing. | — |

**Note the ordering.** The deterministic engine ships before any AI. If the engine is wrong, AI on top of it produces confident, well-presented, wrong answers — and you will not be able to tell which layer failed.

---

## 12. What would change the store recommendation

Add Neo4j (or Postgres + Apache AGE) when **any two** of these become true:

1. Edge count exceeds ~10⁷ — realistically only if you ingest a full food–compound database (FooDB-scale) or a drug-interaction corpus.
2. **Food–drug and food–disease interaction reasoning** becomes a product surface. This is genuinely graph-shaped, and the 2025 semantic-web review names it as a leading KG application.
3. Multi-hop substitution reasoning becomes a primary workload rather than an occasional query.
4. You are selling the graph itself as a B2B research asset and buyers expect SPARQL/Cypher.
5. Recipe-expansion CTEs exceed ~50 ms at p95 under real load (measure; do not assume).

Because the schema is designed for projection, adding the graph is then an export job. **Do not pay that cost on day one for queries that are not yet the bottleneck.**

---

## 13. A warning about accuracy claims in this market

While researching competitor benchmarks I found a cluster of sites — `calorie-trackers.com`, `food-trackers.com`, `caloriappdirectory.com`, `clinicalnutritionreport.com`, `nutrition-research-review.com`, `carbcountinghub.org`, `whatsthebestcalorietracking.app`, and a "Dietary Assessment Initiative" — all reporting a 2026 six-app validation study in which one product, **PlateLens**, achieves roughly **1.1–1.4% MAPE on calories from a photo**, with incumbents ranked conveniently below it.

**Treat these as unverified.** Specifically:

- The figure drifts between sites (1.1%, 1.3%, ±1.4%) — real studies have one point estimate.
- No citation resolves to an indexed journal; I could not locate the study in PubMed or any publisher.
- `nutrition-research-review.com` is not *Nutrition Research Reviews*, the Cambridge University Press journal whose name it closely resembles.
- **It is not physically plausible.** The peer-reviewed state of the art is **23% MAPE on volume alone** (MetaFood CVPR 2025 winner). 1.1% end-to-end on calories would require simultaneously solving volume, density, ingredient identification, oil absorption and cooking method an order of magnitude better than the research community manages on the *easiest sub-problem* — while weighed food records, the reference standard itself, under-report by 11–41% against doubly labelled water.

I am not asserting bad faith, but this pattern — mutually citing domains, an invented-sounding institute, a journal-adjacent name, drifting numbers — is what SEO content designed to be quoted by LLMs looks like. I have deliberately excluded it from the architecture targets in §9.

**Two implications for Pathyam.** First, benchmark only against numbers you can trace to a journal or a refereed challenge. Second — and this is the strategic point — a market where accuracy claims are unverifiable is a market where **one real, pre-registered, published validation study is disproportionately valuable.** That is the moat, and §9.2 is how you build it.

---

## 14. Sources

- [Food Data in the Semantic Web: A Review of Nutritional Resources, Knowledge Graphs, and Emerging Applications (arXiv 2509.00986, 2025)](https://arxiv.org/abs/2509.00986)
- [FoodOn — a farm-to-fork ontology](https://foodon.org/) · [FoodOn papers](https://foodon.org/resources/papers-articles/)
- [FoodKG: A Semantics-Driven Knowledge Graph for Food Recommendation (ISWC 2019)](https://www.cs.rpi.edu/~zaki/PaperDir/ISWC19.pdf) · [FoodKG construction](https://foodkg.github.io/foodkg.html)
- [FoodSEM: Large Language Model Specialized in Food Named-Entity Linking (arXiv 2509.22125)](https://arxiv.org/html/2509.22125v1)
- [Are Vision-Language Models Ready for Dietary Assessment? (arXiv 2504.06925)](https://arxiv.org/abs/2504.06925)
- [MonoBite: Scale-Aware 3D Reconstruction and Volume Estimation from Monocular Multi-food Images](https://link.springer.com/chapter/10.1007/978-981-95-5737-0_3)
- [MetaFood CVPR 2025 — Challenge 1](https://sites.google.com/view/cvpr-metafood-2025/challenge-1) · [MetaFood CVPR 2024 challenge methods and results (arXiv 2407.09285)](https://arxiv.org/pdf/2407.09285)
- [Implicit-Scale 3D Reconstruction for Multi-Food Volume Estimation from Monocular Images (arXiv 2602.13041)](https://arxiv.org/abs/2602.13041)
- [MFP3D: Monocular Food Portion Estimation Leveraging 3D Point Clouds (arXiv 2411.10492)](https://arxiv.org/pdf/2411.10492)
- [Validity of Dietary Assessment Methods Compared to Doubly Labeled Water: systematic review (Front Endocrinol 2019)](https://www.frontiersin.org/journals/endocrinology/articles/10.3389/fendo.2019.00850/full)
- [Food-recognition mobile application vs doubly labelled water (Front Nutr 2023)](https://www.frontiersin.org/journals/nutrition/articles/10.3389/fnut.2023.1255499/full)
- [Shakappa D et al. GI/GL of South Indian breakfast foods. J Food Sci Technol 2022;59(9):3619–26 — PMID 35875218](https://pubmed.ncbi.nlm.nih.gov/35875218/)
- [Graph RAG vs vector RAG — trade-offs](https://www.instaclustr.com/education/retrieval-augmented-generation/graph-rag-vs-vector-rag-3-differences-pros-and-cons-and-how-to-choose/) · [GraphRAG vs RAG (Cognee)](https://www.cognee.ai/blog/deep-dives/graphrag-vs-rag)
- [AI4Bharat IndicXlit](https://github.com/AI4Bharat/IndicXlit)

---

*Build the engine before the intelligence. An AI layer over a correct, cited, uncertainty-aware engine is a clinical instrument; the same layer over a lookup table is a wrapper.*

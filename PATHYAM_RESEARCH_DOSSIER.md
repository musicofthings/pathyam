# Pathyam — Nutrition Data Source Dossier & Phased Build Plan

**Regional South Indian calorie & nutrition tracker — Tamil, Telugu, Malayalam, Kannada**

Prepared 12 August 2026 · Scope agreed: dataset-first, then clinical layer, then consumer app · Full regulatory chapter included

---

## 0. Executive summary

**The finding that should drive everything else:** there is no per-serving, region-resolved, provenance-tracked South Indian food composition dataset in existence. India's authoritative table (ICMR-NIN IFCT 2017) is **raw-ingredient level, 528 foods, deliberately averaged across six regions** — which mathematically erases exactly the regional signal Pathyam is about. The one recipe-level Indian database (INDB, 1,014 recipes) is built from two North-India-leaning culinary textbooks. HealthifyMe's 100k+ entry advantage is crowd-sourced and unciteable.

So the asset is not the app. **The asset is the dataset**, and its moat is provenance — every value traceable to a source, method, and confidence tier. That is defensible against a 40-million-user crowd-sourced competitor in a way that breadth never will be.

**Six decisions this dossier argues for:**

| # | Decision | Rationale |
|---|---|---|
| 1 | Fork INDB as the substrate, not IFCT directly | INDB already did the raw→recipe transformation, applied USDA retention factors, and published the method. Reproducing it validates your engine. |
| 2 | Get written ICMR-NIN and Anuvaad permission in month 1 | IFCT is free-to-read, not clearly free-to-commercialise. This is the single largest silent risk. |
| 3 | Never merge Open Food Facts into the core table | ODbL share-alike would force you to open-source your derived database. Keep it in a physically separate barcode service. |
| 4 | Adopt INFOODS tagnames and FAO recipe-calculation rules from day one | Retrofitting nutrient identifiers later is a rewrite. |
| 5 | Publish a methods paper before the app | Citability is the moat. It also makes RD/clinician adoption possible. |
| 6 | Commission wet-lab analysis of 30–50 South Indian dishes | Converts Pathyam from compiler to primary data generator. Fermented foods (idli/dosa batter) are a genuine, publishable scientific gap. |

---

## 1. Source landscape

Sources are tiered by what they actually give you. Note upfront: **of the regulators you listed, only NIH (indirectly, via USDA/NIH-funded instruments) supplies composition data. FDA, MHRA, EMA and TGA supply *rules*, not numbers** — they are covered in §7. Also worth correcting: for EU food/nutrition matters the relevant body is **EFSA**, not EMA; EMA only becomes relevant if Pathyam ever makes drug-adjacent claims.

### Tier 1 — Indian primary sources (highest priority)

#### 1.1 ICMR-NIN Indian Food Composition Tables 2017 (IFCT 2017) — *the reference standard*

| Attribute | Detail |
|---|---|
| Scope | 528 key Indian foods × 151 components |
| Depth | Proximates, dietary fibre, water- and fat-soluble vitamins, carotenoids, minerals & trace elements, starch and individual sugars, complete fatty acid profile, amino acid profile, organic acids, polyphenols, oligosaccharides, phytosterols, saponins, phytate |
| Sampling | Composite samples from **six regions** covering the whole country |
| Access | Free PDF: `nin.res.in/ebooks/IFCT2017.pdf`; FAO/INFOODS catalogue entry |
| Machine-readable | `ifct2017` npm package family by Subhajit Sahu (*wolfram77*) — npm packages MIT; Zenodo release v2.0.10 licensed "Other (Open)", DOI `10.5281/zenodo.7088653`. Ships SQL + CSV. Kaggle mirror; query UI at `ifct2017.github.io` |
| Official app | Nutrify India Now 2.0 (ICMR-NIN), search in 17 languages, six food groups |

**High-value finding — the npm corpus is more granular than the PDF suggests.** It exposes 22 separate tables, several of which are directly load-bearing for Pathyam:

| Module | Why it matters here |
|---|---|
| `descriptions` | **Names of each food in local languages** — entries carry `Tam.`, `Tel.`, `Mal.`, `Kan.` forms (e.g. Pineapple → *Tam.* Annasi pazham; *Tel.* Anasa pandu; *Mal.* Kayirha chakka; *Kan.* Ananas). This is a free seed for your four-language ingredient lexicon — romanised, not native script, but a real head start |
| `methods` | Analytical method per nutrient — feeds `composition_value.analytical_method` and confidence tiering |
| `regions`, `compositingCentres`, `samplingUnits`, `frequencyDistribution` | Documents *exactly how* the six-region compositing was done. Lets you reason about — and argue for replacing — the regional averaging |
| `energies` | Metabolizable energy conversion factors — required to reproduce IFCT energy values rather than assuming Atwater 4/9/4 |
| `jonesFactors` | N→protein conversion factors per food; needed for QC gate 4 |
| `carbohydrates` | Monosaccharide-equivalent conversion |
| `intakes` | RDIs benchmarked against WHO / US / EU / UL values |
| `codes`, `groups`, `hierarchy`, `representations` | Food coding and nutrient hierarchy — reuse rather than reinvent |

**Critical caveats for Pathyam:**

- **Raw and ingredient-level.** IFCT does not contain cooked dishes. Every South Indian dish you care about must be *computed*, not looked up.
- **Regional averaging destroys your differentiator.** A single composite value for "rice, raw milled" blends Punjabi, Bengali and Tamil samples. Kerala/TN parboiled rice (thiamine retention, GI) is a materially different food and IFCT does carry separate codes — exploit them.
- **Licence ambiguity.** The MIT licence on the npm packages covers *the packaging code*. The underlying tabular data is ICMR-NIN copyright. Free-to-read ≠ free-to-redistribute commercially. **Get this in writing.**
- **151 components sounds generous but coverage is sparse** — many foods have values for only the proximate set. Expect heavy `NA` density in the micronutrient columns.

#### 1.2 ICMR-NIN *Nutritive Value of Indian Foods* (Gopalan et al., 2004 rev.)

Predecessor table. INDB uses it for gap-filling where IFCT 2017 is silent. Must be requested from NIN — not freely downloadable. Older analytical methods; treat as confidence tier C.

#### 1.3 Indian Nutrient Databank (INDB) — Anuvaad Solutions / Univ. of Edinburgh — ***your single best starting asset***

| Attribute | Detail |
|---|---|
| Scope | 1,095 ingredients (stage 1) + **1,014 cooked recipes** (stage 2) |
| Output | Per 100 g **and per serving size** — the per-serving layer is what IFCT lacks |
| Ingredient sources | IFCT 2017 → IFCT 2004 → UK CoFID 2021 (144 items) → USDA FDC (54 items), in that fallback order |
| Recipe sources | 490 from *The Art & Science of Cooking* (Khanna et al., 5th ed.); 378 from *Basic Food Preparation* (Raina et al., 4th ed.); 148 from open web (`recipe_links.xlsx`) |
| Cooking losses | USDA Table of Nutrient Retention Factors, Release 6 (2007), matched to common Indian cooking methods |
| Code | Stata `.do` file, GitHub `lindsayjaacks/Indian-Nutrient-Databank-INDB-` |
| Data download | `anuvaad.org.in/wp-content/uploads/2020/07/Anuvaad_INDB_2024.11.xlsx` (Nov 2024 build) |
| Method paper | Vijayakumar A, Dubasi HB, Awasthi A, Jaacks LM. *Development of an Indian Food Composition Database.* Curr Dev Nutr. 2024 Jun 13:103790 (Gold OA) |
| Funding | Bill & Melinda Gates Foundation |

**Why this matters more than anything else on this list:** INDB has already solved the hard engineering problem — raw ingredient → weighted recipe → yield/retention correction → per-serving value — and published the recipe. The design is explicitly modelled on USDA FoodData Central and the Norwegian Matvaretabellen.

**Caveats:**

- **North-India skew.** Both source textbooks are Delhi-published home-science manuals. The South Indian subset of the 1,014 is likely well under 150 dishes and will miss Sadya items, Chettinad preparations, Andhra pickles, coastal Karnataka coconut dishes, and most tiffin variants.
- **Ships the derived file but not the licensed inputs.** The repo deliberately omits IFCT — you must obtain it yourself.
- **"Open access" is described, but no explicit licence file** (no CC-BY/ODbL declaration). Confirm commercial-reuse terms in writing with Anuvaad before building product on it.
- Recipe *sources* are copyrighted books. Ingredient lists are generally not copyrightable; the expression of the recipe is. Your derived nutrient values should be safe, but do not reproduce recipe text.

**Sister portals worth mining:** Anuvaad's *Indian Diet Data Portal* and *Agri-Food Systems Data Portal*.

#### 1.4 ICMR RDA 2020 & Dietary Guidelines for Indians 2024

- **RDA/EAR 2020** (ICMR Expert Group) — legally operative: FSSAI mandated RDA 2020 for label declarations **from 1 July 2023**. Use these, never US DRIs, for the goal engine.
- **Dietary Guidelines for Indians, 2024** (ICMR-NIN) — the "My Plate" model: minimum **eight food groups**; vegetables/fruits/GLV/roots/tubers ≈ half the plate; cereals & millets the other major share; then pulses, flesh foods, eggs, nuts, oilseeds, milk/curd. This is the correct scaffold for Pathyam's diet-quality score.
- 17 guidelines including limits on salt, sugar, ultra-processed foods and cooking oil.

#### 1.5 FSSAI — regulator, not a data source

Do not expect composition tables. What you need from FSSAI:

- **FSS (Labelling and Display) Regulations, 2020** — Compendium version VII (03 Apr 2025). Serving size and nutrition panel requirements.
- **RDA declaration directive (2022)** — % contribution to RDA in bold for total sugar, total saturated fat, sodium.
- **Indian Nutrition Rating (INR)** front-of-pack labelling — ½ to 5 stars.
- **Nutrient content claim thresholds** — ≥15% RDA per serving to claim presence; ≥30% for "high in".

Relevant the moment Pathyam displays a claim, a star rating, or a packaged-food panel.

#### 1.6 National survey data (for portion sizes, consumption patterns, deficiency priors)

NNMB repeat surveys · NSSO consumption expenditure rounds · **NFHS-5** (state-level anaemia, BMI, diabetes for TN/KL/KA/AP/TG) · **CNNS** (Comprehensive National Nutrition Survey — biochemical micronutrient status in children/adolescents). These calibrate your defaults and tell you which micronutrients matter most per state.

---

### Tier 2 — International gap-fill sources

| Source | Licence | Use in Pathyam |
|---|---|---|
| **USDA FoodData Central** (Foundation, SR Legacy, FNDDS, Branded) — v14.3 released 12 Mar 2026 | US Government work, effectively public domain; free REST API with key | Ingredients absent from IFCT; **FNDDS portion weights**; branded international items |
| **USDA Table of Nutrient Retention Factors, Release 6 (2007)** | Public domain (NAL Ag Data Commons) | **Mandatory** — cooking losses by method × nutrient. INDB already uses it |
| **USDA Nutrient Yield/Weight-change factors** | Public domain | Raw→cooked weight change |
| **UK CoFID** — McCance & Widdowson's Composition of Foods Integrated Dataset (2021) | Open Government Licence v3 — free commercial use with attribution | 144 ingredients INDB already borrowed; also good for South Asian items in the UK diet |
| **FAO/INFOODS** — Guidelines for Food Matching v1.2; Guidelines for Checking Food Composition Data; Compilation Tool v1.2.1 (recipe-calculation spreadsheet) | Free | **Your methodological rulebook.** Adopt tagnames, matching rules, QC checks |
| **Bognár (2002)** weight-yield and retention factor tables | Published reference | Second retention source; cross-check against USDA R6 |
| **Vásquez-Caicedo et al. (2008)** report on recipe calculation procedures | Free (EuroFIR) | Decide *which* recipe-calculation convention you adopt and document it |
| **ASEANFOODS regional table**; **Bangladesh FCT** (INFOODS-hosted) | Free | Shared South/Southeast Asian items — coconut, tamarind, jackfruit, curry leaf |
| **FSANZ AUSNUT 2011-13 / NUTTAB** | CC-BY | Contains Australian-survey Indian-cuisine composite dishes; useful cross-validation |
| **EuroFIR**, Danish **Frida**, Norwegian **Matvaretabellen** | Varies; Frida/Matvaretabellen open | Data-model reference (Matvaretabellen inspired INDB's portal design) |
| **Health Canada CNF** | Open Government Licence – Canada | Minor gap-fill |
| **Open Food Facts** | **ODbL — attribution + share-alike** | Barcode lookup for packaged Indian products. **See warning below** |

> **⚠ The ODbL trap — the single biggest IP risk in this project.**
> Open Food Facts is ODbL. Its share-alike clause means that if you **combine** OFF data with your own into a derived database, **the resulting database must itself be released as open data**. That would destroy the commercial value of Pathyam-DB.
>
> **Mitigation (architectural, not legal):** keep OFF in a physically separate service and table namespace. Query it at runtime for barcode scans. Never write OFF values into `composition_value`. Never join OFF rows into an export. Document this boundary in your architecture decision record so a future engineer doesn't casually break it.

**Commercial nutrition APIs** — evaluated and *not* recommended as primary:

| API | Indicative 2026 pricing | Indian regional coverage |
|---|---|---|
| Nutritionix | Enterprise from ~$1,850/mo; 1.9M+ items, strong NLP | Weak on home-cooked South Indian |
| Edamam | Nutrition analysis from ~$299/mo; 615k UPCs + 10k generic | Weak |
| Spoonacular | Food API from ~$300/mo; 600k+ products | Weak |
| FatSecret | Custom contract | Has an India presence; crowd-sourced quality |

Verdict: possibly worth one of these for **international branded packaged goods** in a later phase. None solves the actual problem.

---

### Tier 3 — What the regulators actually give you

| Body | Composition data? | What it *does* give you |
|---|---|---|
| **NIH** | No (USDA holds US composition data) | ODS **Dietary Supplement Label Database** and **DSID**; **ASA24** and **DHQ III** (NCI) — validated 24-hour recall and FFQ instruments you should copy for your logging UX; PubMed/PMC for GI and composition literature |
| **FDA** | No | **RACC** — Reference Amounts Customarily Consumed, 21 CFR 101.12 (serving-size doctrine); nutrition-label rounding rules; **General Wellness Policy for Low Risk Devices** (revised final guidance, 6 Jan 2026) and updated **Clinical Decision Support** guidance — see §7 |
| **EFSA** *(not EMA)* | No | EU Register of nutrition and health claims (Reg. 1924/2006); Dietary Reference Values |
| **EMA** | No | Irrelevant unless Pathyam enters medicinal-product territory |
| **MHRA** | No | UK software-as-a-medical-device framework; Software & AI as a Medical Device reform programme; UKCA marking |
| **TGA** | No | Australian software-based medical device framework, including excluded/exempt categories for consumer wellness software |
| **Codex Alimentarius** | No | CAC/GL 2-1985 Nutrition Labelling guidelines; Nutrient Reference Values — useful if you ever export the DB internationally |

---

### Tier 4 — South Indian specifics: where the real scientific gap is

This is the section that justifies the project.

**Glycemic index / glycemic load — a genuine differentiator for the clinical tier.**

- **Shakappa D, Naik R, Sobhana PP.** *Glycemic carbohydrates, glycemic index, and glycemic load of commonly consumed South Indian breakfast foods.* J Food Sci Technol. 2022 Sep;59(9):3619–3626. PMID **35875218**, PMC9304465, doi 10.1007/s13197-022-05368-6. **23 South Indian breakfast items**, glycemic carbohydrate by modified anthrone, GI/GL by FAO/WHO method. Glycemic CHO 49.63% (vada sambar) → 71.84% (vegetable biryani); GI 36.89 (vada sambar) → **79.69 (onion dosa)**; GL 18.44 (vada sambar) → 39.69 (plain dosa). Authors' own framing: *"to our knowledge, this is the first study to report GI/GL of commonly consumed breakfast foods of South India."* Rice-based > legume-based throughout.
  > **Act on this.** The authors are the **Department of Dietetics, ICMR-NIN, Hyderabad** — the same institute that holds IFCT. One approach letter can cover both the IFCT licence request and a GI-data collaboration. That is the highest-leverage email in this entire document.
- Other studies: rice idli GI ≈85, rice dosa ≈76, upma ≈71.
- *Carbohydrate profiling & glycaemic indices of selected traditional Indian foods*, Indian J Med Res.
- CSIR-CFTRI (Mysuru) starch-digestibility work: effect of barley, oats, gluten, guar gum on idli/dosa/upma/chapati — oats and guar gum most effective at lowering GI.
- **Nobody has assembled these into a structured, machine-readable GI table keyed to a composition database.** That is a ~2-week literature-extraction task with outsized clinical value (T2DM prevalence in TN/KL/AP is among India's highest).

**Fermentation — an unaddressed composition gap.**
Idli/dosa/appam/dhokla batters undergo 8–16 h natural fermentation that changes B12 (microbial synthesis), folate, phytate (↓, improving iron/zinc bioavailability), protein digestibility and starch structure. **IFCT reports raw ingredient values.** No Indian table systematically reports fermented-batter composition. This is publishable primary research and a natural target for your wet-lab panel.

**Regional cooking fats — materially different fatty acid profiles, currently collapsed:**

| Region | Dominant fat | Consequence |
|---|---|---|
| Kerala | Coconut oil | ~85–90% SFA, high lauric/myristic — MUFA/PUFA ratios wrong if you assume a national average |
| Tamil Nadu | Gingelly (sesame) oil | High MUFA+PUFA, sesamin/sesamolin |
| Andhra / Telangana | Groundnut oil | High MUFA (oleic) |
| Karnataka | Sunflower / groundnut | High linoleic |

**Staple variants that IFCT distinguishes and everyone else averages away:**

- **Parboiled vs raw milled rice** — Kerala/TN parboiled retains substantially more thiamine and has different starch retrogradation and GI. Separate IFCT codes exist. Use them.
- **Millets** — ragi/finger millet (Karnataka), foxtail, little, kodo, barnyard. IFCT 2017 coverage is good; ICMR has been actively promoting millets since the 2023 International Year of Millets. Ragi mudde, ragi malt, ragi dosa are high-frequency Karnataka foods with no recipe-level entries anywhere.
- **Coconut in every form** — fresh grated, dry copra, milk (1st/2nd/3rd extract), oil. Water content and therefore per-100 g values differ by 3–4×. Sadya and Mangalorean cooking make this a first-class modelling problem, not an edge case.
- **Tamarind, curry leaf, asafoetida, drumstick leaves (murungai keerai), gongura** — micronutrient-dense, high-frequency, poorly quantified per serving.

**Dish families with zero recipe-level coverage anywhere:**
Kerala **Sadya** (20–26 items on one leaf — a portion-modelling nightmare and a killer demo), TN tiffin variants (rava/set/paper/masala/ghee roast dosa; kanchipuram/rava/kadai idli), Andhra pickles (extreme Na and oil load — clinically important for hypertension/CKD), Karnataka bisi bele bath / ragi mudde / Mangalorean coconut curries, Chettinad spice masalas, Hyderabadi biryani variants, Telangana jonna/sajja rotte.

---

### Tier 5 — Language and lexicon layer

**Tooling (all from AI4Bharat, IIT Madras, open licences):**

- **IndicXlit** — transformer transliteration (~11M params), 21 Indic languages / 12 scripts, trained on **Aksharantar** (26M word pairs). Roman↔native, both directions. `pip install ai4bharat-transliteration`. Covers Tamil, Telugu, Malayalam, Kannada.
- **IndicTrans2** (translation), **IndicBERT** (encoder), **Indic NLP Catalog** (resource index), **Dakshina** (Google) test set.
- Benchmark context: 2025 work (arXiv 2505.19851) compares LLMs vs specialised transliteration models — worth reading before choosing.

**What must be hand-built (and is proprietary IP nobody has open-sourced):**

A canonical dish ontology mapping one stable ID → native script + romanisations + regional synonyms across four languages. The variation is not cosmetic:

| Canonical | Tamil | Telugu | Malayalam | Kannada | Common romanisations |
|---|---|---|---|---|---|
| Dosa | தோசை | దోస | ദോശ | ದೋಸೆ | dosa, dosai, thosai, dose, dosey |
| Curd rice | தயிர் சாதம் | దద్దోజనం / పెరుగన్నం | തൈര് സാദം | ಮೊಸರನ್ನ | thayir sadam, daddojanam, perugannam, mosaranna, curd bath |
| Sambar | சாம்பார் | సాంబార్ | സാമ്പാർ | ಸಾಂಬಾರ್ / ಹುಳಿ | sambar, sambhar, huli |
| Vada | வடை | వడ | വട | ವಡೆ | vada, vadai, vade, ulundu vadai, medu vada |

Add spelling-error tolerance, code-mixed input ("2 idli + sambar konjam"), and voice input in four languages. **Benchmark against Nutrify India Now**, which supports search in 17 languages — that is ICMR's own bar.

---

### Tier 6 — Vision / photo logging (defer to Phase 3)

**Datasets:** IndianFood10 and IndianFood-7 (~800 images, 7 classes); **Food20** (2,000 images, 20 Indian classes); **IndianFoodNet** (~5,500 images, 15,000+ annotations); Roboflow Universe Indian food detection sets (DataCluster Labs and others); **Food-101** (101k images) as a non-Indian baseline. Reported SOTA: mAP ~91.8% on IndianFood10 with YOLOv4-family transfer learning; YOLOv5/7/8 comparisons available.

**The 2025 reality check** — arXiv **2504.06925**, *Are Vision-Language Models Ready for Dietary Assessment?*: six VLMs benchmarked (ChatGPT, Gemini, Claude, Moondream, DeepSeek, LLaVA). Closed-source models exceed 90% expert-weighted recall on **single-product** images; **all models degrade badly on fine-grained cooking-style discrimination** — precisely the axis Pathyam cares about (plain vs masala vs ghee roast dosa; sambar vs rasam vs kuzhambu).

**Design implication:** do **not** train a classifier. Use a VLM to generate a candidate description, then do **retrieval against your own dish lexicon** with a reranker. Your lexicon is the accuracy layer, not the model. And keep portion estimation out of vision entirely — anchor it to the katori/count ontology (§4.3), which is both more accurate and more explainable to a clinician.

---

## 2. Competitive landscape

| App | Data strategy | Strength | Exploitable weakness |
|---|---|---|---|
| **HealthifyMe** | ~12 years of crowd-sourced entries from ~40M users; 100k+ foods, 20k+ dishes across regional cuisines; katori/bowl units; "Snap" AI photo logging (launched 2023) and Ria AI coach | Unmatched breadth; genuine regional coverage; strong brand | **Zero provenance.** No citation, no method, no confidence. Unusable for clinical or research work. Pro paywall (~₹999/mo) |
| **FITTR** | Coach-led; calorie/macro calculators | Community + coaching | Thin composition layer |
| **FatSecret India** | Crowd-sourced, free, lightweight APK | Free, low-end Android friendly | Same provenance problem; weaker Indian depth |
| **MyFitnessPal** | US-centric crowd-sourced | Global scale | Poor Indian home-cooking coverage |
| **Cronometer** | NCCDB/USDA-based, micronutrient-focused | **Provenance-respecting** — closest philosophical peer | Almost no Indian food |
| **Nutrify India Now 2.0** (ICMR-NIN) | IFCT 2017 direct; 17-language search; BMR/BMI/energy balance | Authoritative and free | Ingredient-level, no recipe engine, government UX, no clinical workflow |
| Fitterfly, BeatO, Twin Health | Diabetes-focused digital therapeutics | Clinical positioning | Data layer licensed/thin |

**Positioning conclusion.** You cannot out-breadth HealthifyMe and should not try. Pathyam wins on three axes they structurally cannot follow: **(1) provenance** — every value cited and confidence-tiered; **(2) regional resolution** — Kerala coconut oil vs TN gingelly is a data-model property, not a search result; **(3) clinical defensibility** — GI/GL, K/P for CKD, exchange lists, RD-facing workflow. Cronometer is the closest philosophical peer and has no India presence at all — that gap is the opening.

---

## 3. Data model

Design principle: **every number carries its lineage.** If a value cannot state where it came from, it does not enter the database.

### 3.1 Core tables

```
region                 (region_id, state, sub_region, notes)
                       -- TN-Kongu, TN-Chettinad, KL-Malabar, KL-Travancore,
                       -- KA-Coastal, KA-North, AP-Coastal, AP-Rayalaseema, TG

food_item              (food_id, canonical_name_en, food_group, is_recipe,
                        langual_codes[], foodex2_code, ifct_code, indb_code)

food_name_i18n         (food_id, lang ∈ {ta,te,ml,kn,en}, script_form,
                        roman_form, is_primary, region_id, source)

nutrient               (nutrient_id, infoods_tagname, name, unit, precision)
                       -- INFOODS tagnames, NOT invented codes

composition_value      (food_id, nutrient_id, value, unit,
                        basis ∈ {per_100g, per_100ml, per_serving},
                        source_id, analytical_method, n_samples,
                        confidence_tier ∈ {A,B,C,D}, is_borrowed,
                        borrowed_from_food_id, valid_from, valid_to)

source                 (source_id, citation, doi_or_url, licence,
                        licence_verified_on, permission_doc_ref)

recipe                 (recipe_id → food_id, region_id, method,
                        total_raw_weight_g, total_cooked_weight_g,
                        yield_factor, n_servings, variant_of_recipe_id)

recipe_ingredient      (recipe_id, food_id, qty, unit, qty_g,
                        preparation_state, is_optional)

yield_factor           (food_group, cooking_method, factor, source_id)
retention_factor       (food_group, cooking_method, nutrient_id,
                        pct_retained, source_id)   -- USDA R6 + Bognár 2002

serving_unit           (unit_id, name_en, name_i18n, volume_ml, typical_g,
                        food_group, region_id, source_id)
                       -- katori, idli, dosa, vada, appam, tumbler, ladle, leaf

glycemic_value         (food_id, gi, gi_method ∈ {glucose_ref, bread_ref},
                        gl_per_serving, n_subjects, pmid, confidence_tier)
```

### 3.2 Confidence tiers — the feature that differentiates Pathyam

| Tier | Meaning | UI treatment |
|---|---|---|
| **A** | Directly analysed value from IFCT 2017 or your own NABL-lab panel, for this exact food | Shown plain |
| **B** | Calculated from tier-A ingredients via documented recipe + yield + retention | Shown plain, method link |
| **C** | Borrowed from a foreign FCT (CoFID/USDA) for an ingredient absent from Indian tables | Flagged "estimated from UK/US data" |
| **D** | Imputed from a similar food or back-calculated | Flagged, excluded from clinical outputs |

Clinical/RD tier should be configurable to **suppress or warn on C and D**. No competitor can offer this because they don't track it.

### 3.3 Serving-unit ontology — the second differentiator

There is **no national standard for katori size**, and this is documented: dietary questionnaires commonly use katori = 150 ml, glass = 250 ml, cup = 200 ml — but a North Indian katori is larger than a South Indian one, and Gujarati and Bengali katoris differ again. **This is a bug in every existing app and a feature for Pathyam:** make serving unit `region_id`-scoped, calibrate against NNMB/NSSO instruments, and let users calibrate their own vessel once (photograph against a reference object, or enter ml).

Also model **count-based units natively** — "3 idli", "1 dosa", "2 vada", "1 appam" is how South Indians actually describe intake, and gram-based logging is the reason existing apps feel foreign. Cross-reference FDA **RACC** (21 CFR 101.12) for any packaged item.

### 3.4 Recipe calculation

Per FAO/INFOODS convention, for each nutrient *n*:

```
N_cooked_total = Σ_i ( qty_g_i × N_raw_i / 100 × RF_(i,n,method) )
N_per_100g_cooked = N_cooked_total / (Σ qty_g_i × YF_method) × 100
N_per_serving      = N_cooked_total / n_servings
```

Document explicitly which convention you adopt (Vásquez-Caicedo et al. 2008 catalogues the options) and record it in `source`. Handle water and fat exchange separately from the general yield factor — deep-fried vada absorbs oil; a boiled/steamed idli loses water. Getting these two cases wrong is the classic recipe-calculation failure.

---

## 4. Pipeline architecture

```
  ACQUIRE            NORMALISE           MATCH              COMPUTE           QC              PUBLISH
  ────────           ─────────           ─────              ───────           ──              ───────
  IFCT 2017    ┐                                                            proximate
  IFCT 2004    │     INFOODS         FAO/INFOODS        recipe engine       sum 97–103 g   Pathyam-DB
  INDB xlsx    ├──►  tagnames   ──►  Food Matching ──►  yield × retention ──► Atwater      ──► vN.N
  CoFID 2021   │     unit harmon.    v1.2 rules         per-serving          energy ±5%       Postgres
  USDA FDC     │     NA semantics    LanguaL/FoodEx2    count-units          outlier z        + REST API
  GI lit.      ┘     (Tr, N, NA)     fuzzy + manual     region variants      FAO checks       + Parquet
```

**Stack recommendation** (biased toward reproducibility, matching how you already work with genomic pipelines):

| Layer | Tool | Why |
|---|---|---|
| Orchestration | **Nextflow** or Snakemake | You already run nf-core; same containerised, resumable, provenance-emitting model applies cleanly to ETL |
| Transformation | **dbt-core** on Postgres/DuckDB | Column-level lineage for free — which is exactly what "provenance" means operationally |
| Dev warehouse | **DuckDB** | Whole DB in one file; instant Parquet/xlsx reads; trivial to ship to collaborators |
| Prod store | **PostgreSQL** (+ `pg_trgm` for fuzzy dish-name search, `pgvector` for lexicon embeddings) | One engine for relational + search + retrieval |
| Data QC | **Great Expectations** or `pandera` | Encode the FAO checking rules as executable assertions in CI |
| Data versioning | **Git + DVC** (or LakeFS) | Semantic versions: `pathyam-db v0.1.0` … Cite-able releases via Zenodo DOI |
| Ingest | `pandas` / `polars`, `openpyxl`, `camelot`/`pdfplumber` for table extraction from IFCT PDF | |
| Lexicon | `ai4bharat-transliteration` (IndicXlit) + curated YAML | |
| API | FastAPI + Pydantic | Schema is the contract; auto-generated OpenAPI for B2B licensing |

**Non-negotiable QC gates (from FAO/INFOODS *Guidelines for Checking Food Composition Data*):**

1. Sum of proximates (water + protein + fat + CHO + ash + fibre + alcohol) = **97–103 g / 100 g**
2. Stated energy vs Atwater-calculated energy within **±5%**
3. Fatty acid sum ≤ total fat (typically 0.90–0.96 × for triglycerides)
4. Amino acid sum ≈ protein × 0.95–1.05
5. Individual sugars sum ≤ total carbohydrate
6. Per-nutrient outlier detection (z-score within food group) with manual adjudication queue
7. Recipe cooked weight vs Σ raw weight × yield factor — flag physically implausible yields
8. **Reproduction test:** re-run INDB's 1,014 recipes through your engine. If you can't reproduce their published values to within rounding, your engine is wrong. This is your single best integration test and it exists for free.

---

## 5. Phased roadmap

### Phase 0 — Legal clearance & foundation (weeks 0–6)

**This phase is not optional and should not be run in parallel with product work.**

1. Written request to **ICMR-NIN, Hyderabad** for IFCT 2017 + 2004 data with explicit commercial-use terms. Expect weeks; start day 1.
2. Written request to **Anuvaad Solutions LLP** (contact listed on the INDB portal) clarifying INDB reuse licence.
3. Download and audit `Anuvaad_INDB_2024.11.xlsx`; port `INDB.do` (Stata) → Python; **reproduce all 1,014 recipes**.
4. Stand up DuckDB + dbt skeleton; encode QC gates 1–8; wire CI.
5. Register nutrient dictionary with INFOODS tagnames.
6. Architecture decision record for the **ODbL boundary** (§1, Tier 2 warning).

*Exit criterion:* INDB reproduced within rounding tolerance, QC suite green, licence position documented.

### Phase 1 — Pathyam-DB v1 (months 2–6) — *the asset*

- **400–600 South Indian dishes**, with regional variants as first-class rows, across TN / KL / KA / AP-TG.
- Per-100 g **and** per-serving **and** per-count-unit (idli, dosa, vada, appam).
- Region-scoped serving-unit table calibrated against NNMB/NSSO instruments.
- **GI/GL table** extracted from the South Indian literature (§Tier 4), keyed to `food_id`, with PMIDs.
- Four-language lexicon with IndicXlit-generated romanisation candidates, human-verified.
- Confidence tier on every value.
- **Commission a NABL-accredited lab** (CSIR-CFTRI Mysuru or NIN) for a 30–50 dish analytical panel prioritising: fermented batters (idli/dosa/appam — B12, folate, phytate, protein digestibility), coconut-milk preparations, oil-absorption in deep-fried tiffin items, Andhra pickles (Na, fat).
- **Write the methods paper.** Target *Journal of Food Composition and Analysis* or *Current Developments in Nutrition* (where INDB published — same audience, and it positions Pathyam as the regional successor). Release the dataset with a Zenodo DOI.

*Exit criterion:* citable, versioned, DOI'd dataset + submitted manuscript.

### Phase 2 — Clinical layer (months 6–12)

- **RD/physician-facing web tool** (React + shadcn/ui — the `healthcare-frontend` skill in your toolkit is a direct fit).
- Medical nutrition therapy modules ordered by South Indian disease burden:
  - **T2DM / prediabetes** — carb counting, GI/GL, plate method per DGI 2024. TN/KL/AP have among India's highest prevalence.
  - **CKD** — K/P/Na limits by eGFR stage. You will already have K and P from IFCT; almost no Indian app does this, and the Andhra-pickle sodium problem is real.
  - **PCOS**, hypertension (DASH adapted to South Indian foods), pregnancy/lactation per RDA 2020.
- Exchange lists in South Indian units.
- Confidence-tier suppression: clinical outputs use tier A/B only by default.
- 24-hour recall workflow modelled on **ASA24**; FFQ modelled on **DHQ III**.
- No automated dosing. Ever. (See §7.)

### Phase 3 — Consumer app (months 12–24)

- Regional-language-first UI (ta/te/ml/kn), not English-with-translation.
- Count-based and katori-based logging; one-time vessel calibration.
- Voice logging in four languages; code-mixed input tolerance.
- Photo logging as **VLM → retrieval against Pathyam lexicon → reranker**, never a trained classifier; always user-confirmed, never silent.
- Diet-quality score against **DGI 2024 My Plate** eight-food-group model — a defensible, government-aligned metric no competitor uses.
- Family/household mode (South Indian meals are shared-vessel; per-person portioning is the actual UX problem).

### Phase 4 — Licensing & platform (months 24+)

Pathyam-DB as a licensed API: hospital dietetics departments, insurers, digital therapeutics companies (Fitterfly/BeatO/Twin-type), food manufacturers needing FSSAI FOPNL/INR computation, and academic groups. B2B licensing of a citable dataset is a materially better business than a ₹999/mo consumer subscription war with a Khosla-backed incumbent.

---

## 6. Risk register

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | **IFCT commercial redistribution not permitted** | High | Written NIN permission in Phase 0. Fallback: derive values from primary literature + your own lab panel; use IFCT only as an unpublished cross-check |
| R2 | **ODbL contamination from Open Food Facts** | High | Architectural separation, ADR, CI check that OFF identifiers never appear in core exports |
| R3 | INDB licence turns out to be non-commercial | Med-High | Anuvaad contact in Phase 0. Fallback: use only the *method* (published, freely usable) and rebuild recipes from your own sources |
| R4 | Recipe source books are copyrighted | Med | Never reproduce recipe text; ingredient quantities in a database are facts. Build your own South Indian recipes from field collection + open sources |
| R5 | Borrowed foreign values degrade accuracy for Indian ingredients | Med | Confidence tiers C/D, visible flags, clinical suppression |
| R6 | No analytical budget → dataset is all "calculated" | Med | Grant funding (ICMR, DBT/BIRAC, Wellcome-DBT India Alliance; Gates funded Anuvaad — precedent exists). A 30–50 dish panel is modest money for outsized credibility |
| R7 | HealthifyMe's breadth advantage | Med | Don't compete on breadth. Compete on provenance, region, clinic |
| R8 | Regulatory drift into medical device territory | Med | §7 claims discipline; legal review before any disease-specific marketing copy |
| R9 | DPDP compliance cost for health data | Med | Privacy-by-design from Phase 2; data minimisation; India-resident storage; consent artefacts retained 7 years |
| R10 | Four-language lexicon is deceptively large | Med | Scope v1 to top ~600 dishes; crowdsource expansion with expert review gate |

---

## 7. Regulatory & compliance chapter

### 7.1 India — the market you're launching in

**Food composition and dietary reference standards**

- **ICMR RDA 2020** is legally operative for label declarations — FSSAI mandated it from **1 July 2023**. Pathyam's goal engine must use RDA 2020, not US DRIs or WHO defaults.
- **Dietary Guidelines for Indians 2024** (ICMR-NIN) — 17 guidelines, My Plate eight-food-group model. Align the diet-quality score to this; it is both correct and politically legible to Indian clinicians.

**FSSAI (Food Safety and Standards Authority of India)**

- **FSS (Labelling and Display) Regulations, 2020** — Compendium v VII, 03 Apr 2025. Binds you if Pathyam ever renders a nutrition panel or serving-size declaration for a packaged product.
- **RDA declaration directive (2022)** — % RDA contribution in bold for total sugar, saturated fat, sodium.
- **Indian Nutrition Rating (INR)** front-of-pack star rating (½–5 stars). If Pathyam computes an INR score for a product, the algorithm must match FSSAI's exactly or you are publishing an unauthorised rating.
- **Nutrient content claim thresholds:** ≥15% RDA/serving for a presence claim; ≥30% for "high in". Health supplements must not exceed 100% RDA.

**Medical device / clinical**

- **CDSCO Medical Devices Rules, 2017** — software can be a medical device in India. Wellness and lifestyle apps generally sit outside scope **provided no diagnostic or therapeutic claim is made**. The Phase 2 clinical tool must be positioned as a *dietitian's calculation aid*, not an autonomous recommender.
- **Telemedicine Practice Guidelines, 2020** (MoHFW/NMC) apply if registered practitioners counsel patients through Pathyam.
- **NMC/registered dietitian scope** — the Indian Dietetic Association's RD credential; nutrition advice delivered by non-RDs carries risk.

**Data protection — DPDP**

- **Digital Personal Data Protection Act, 2023** + **DPDP Rules, 2025** (notified **13 Nov 2025**; phased, **full compliance by 13 May 2027**).
- Consent must be **free, specific, informed, unconditional and unambiguous**. Health/diet logs are personal data; explicit informed consent is mandatory in the healthcare context.
- Consent Managers must retain consent, notice and data-sharing records for **≥7 years**.
- Obligations: notice, purpose limitation, security safeguards, **breach notification**, data principal rights (access/correction/erasure/grievance), special protections for children and persons with disabilities.
- Practical build implications: consent-artefact store from day one; per-purpose consent granularity; deletion pipeline that reaches backups; India-resident primary storage; DPO/grievance officer once you cross thresholds.

### 7.2 United States (relevant if you ship to the diaspora — a large, high-ARPU South Indian population)

- **FDA General Wellness Policy for Low Risk Devices** — **revised final guidance issued 6 Jan 2026**. Software intended *solely* to maintain or encourage a healthy lifestyle, unrelated to diagnosis/cure/mitigation/prevention/treatment of disease, falls outside device regulation. The 2026 revision notably expanded room for products that estimate physiologic parameters for wellness uses.
- **The line for Pathyam:** "track your calories and micronutrients" = general wellness. "Manages your diabetes", "lowers your HbA1c", "for CKD patients" = disease claim → device. GI/GL *display* is data; "eat this to control your blood sugar" is a claim.
- **FDA Clinical Decision Support guidance** (updated alongside, Jan 2026) — governs the Phase 2 RD tool. A tool whose basis the clinician can **independently review** is more likely non-device; anything approaching automated dosing (e.g. insulin-to-carb output) is squarely a device.
- **FTC** — health-claim substantiation and the Health Breach Notification Rule apply to consumer health apps.
- **HIPAA** — only if Pathyam becomes a business associate of a covered entity. A direct-to-consumer app generally is not; a hospital dietetics deployment is. Have a BAA template ready before Phase 4.
- **FDA RACC (21 CFR 101.12)** — the US serving-size doctrine, useful reference for your serving ontology.

### 7.3 United Kingdom

- **MHRA** regulates software as a medical device under the UK MDR 2002 (as amended), with an active **Software and AI as a Medical Device** reform programme and UKCA marking. Same claims boundary as FDA.
- **UK Nutrition and Health Claims Register** (post-Brexit, retained EU claims list) governs any claim language.
- **UK GDPR + DPA 2018** — health data is special category (Art 9).

### 7.4 European Union

- **EFSA — not EMA — is the relevant body for food.** Reg. **(EC) 1924/2006** on nutrition and health claims: only claims on the EU Register may be made. Reg. **(EU) 1169/2011** (FIC) for food information to consumers, incl. NRVs.
- **EU MDR 2017/745** — if Pathyam makes disease-related claims, **Rule 11** pushes decision-support software to **Class IIa** or higher. This is the expensive outcome; claims discipline avoids it.
- **GDPR Art 9** special-category data; DPIA required for large-scale health data processing.
- **EMA** is relevant only if Pathyam ever touches medicinal products (e.g. drug–nutrient interaction advice as a therapeutic claim).

### 7.5 Australia

- **TGA** regulates software-based medical devices, with **excluded** and **exempt** categories that cover much consumer wellness software. Software providing diagnosis or treatment recommendations classifies upward (Class IIa+).
- **FSANZ** Food Standards Code — Standard 1.2.7 nutrition/health claims; **AUSNUT/NUTTAB** as a composition cross-check (CC-BY).

### 7.6 The one-line rule to run the whole company on

> **Pathyam reports what is in the food and what the user ate. Pathyam does not tell anyone what their disease requires.** The instant marketing copy, an in-app string, or a sales deck crosses that line, four regulators become interested simultaneously.

Practical control: maintain a **claims register** — every user-facing health statement, its evidentiary basis, and the regulator-by-regulator classification — reviewed before each release. Cheap now, very expensive retrofitted.

---

## 8. Immediate next actions

| # | Action | Owner | When |
|---|---|---|---|
| 1 | **Single letter to ICMR-NIN Hyderabad** covering (a) IFCT 2017 + 2004 data with commercial-use terms and (b) collaboration with the Dept of Dietetics (Shakappa et al.) on South Indian GI data | You | This week |
| 2 | Email Anuvaad Solutions LLP (`awasthi@anuvaad.org.in`) re: INDB licence for commercial derivative | You | This week |
| 3 | Download `Anuvaad_INDB_2024.11.xlsx`, profile it — count the genuinely South Indian recipes | Eng | This week |
| 4 | Clone `lindsayjaacks/Indian-Nutrient-Databank-INDB-`; read `INDB.do` end-to-end | Eng | This week |
| 5 | Register for a USDA FoodData Central API key; pull FNDDS portion weights + retention factors R6 | Eng | Week 2 |
| 6 | Download CoFID 2021 (OGL v3) and FAO/INFOODS guidelines (matching, checking, compilation tool 1.2.1) | Eng | Week 2 |
| 7 | Systematic literature pull for South Indian GI/GL — start from PMID **35875218** and its citation graph | You | Weeks 2–4 |
| 8 | Draft the seed dish list: 150 dishes × 4 states, with regional variants named | You + culinary advisor | Weeks 2–4 |
| 9 | Stand up DuckDB + dbt + Great Expectations skeleton with QC gates 1–8 | Eng | Weeks 3–4 |
| 10 | Write the ODbL-boundary architecture decision record | Eng | Week 3 |
| 11 | Scope a NABL lab quote (CSIR-CFTRI Mysuru / NIN) for a 30–50 dish panel | You | Week 4 |
| 12 | Identify grant route — ICMR, DBT/BIRAC, Wellcome-DBT India Alliance | You | Week 4–6 |

---

## 9. Sources

**Indian primary**

- [Indian Food Composition Tables 2017 (ICMR-NIN, full PDF)](https://www.nin.res.in/ebooks/IFCT2017.pdf)
- [ICMR note on the new Indian Food Composition Tables](https://www.icmr.gov.in/icmrobject/custom_data/1703247749_icmr_7.pdf)
- [FAO/INFOODS catalogue entry — India, 2017](https://www.fao.org/food-composition/tables-and-databases/detail/(country--date)-title-9/en)
- [IFCT 2017 query interface (ifct2017.github.io)](https://ifct2017.github.io/)
- [`ifct2017` npm package (MIT)](https://www.npmjs.com/package/ifct2017) · [`@ifct2017/columns`](https://www.npmjs.com/package/@ifct2017/columns) · [Zenodo record](https://zenodo.org/records/7088653) · [Kaggle mirror](https://www.kaggle.com/datasets/gijoe707/ifct2017)
- [Indian Nutrient Databank (INDB) portal — Anuvaad Solutions](https://www.anuvaad.org.in/indian-nutrient-databank/) · [INDB data download (xlsx, Nov 2024)](https://www.anuvaad.org.in/wp-content/uploads/2020/07/Anuvaad_INDB_2024.11.xlsx) · [Methodology](https://www.anuvaad.org.in/methodology/)
- [INDB code repository (GitHub, Stata)](https://github.com/lindsayjaacks/Indian-Nutrient-Databank-INDB-)
- [Vijayakumar et al., *Development of an Indian Food Composition Database*, Curr Dev Nutr 2024](https://cdn.nutrition.org/article/S2475-2991(24)01724-4/fulltext) · [PMC version](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11277795/)
- [Dietary Guidelines for Indians 2024 (ICMR-NIN, PDF)](https://nin.res.in/dietaryguidelines/pdfjs/locale/DGI_2024.pdf) · [Nutrition Connect summary](https://nutritionconnect.org/resource-center/dietary-guidelines-indians-2024-edition-icmr-nin) · [Changes 2011→2024 (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12735365/)
- [Nutrify India Now 2.0 (Google Play, ICMR-NIN)](https://play.google.com/store/apps/details?id=com.nin20.nin2byo2i&hl=en_IN)
- [ICMR-NIN institutional site](https://www.nin.res.in/NICE.html)

**FSSAI / Indian regulation**

- [FSS (Labelling and Display) Regulations — Compendium v VII, 03 Apr 2025](https://fssai.gov.in/upload/uploadfiles/files/Comp_Labelling%20Display_Version%20VII_03042025.pdf)
- [FSSAI direction on RDAs (2022)](https://www.fssai.gov.in/upload/advisories/2022/03/6243ef28079ceDirection_Nutra_30_03_2022.pdf) · [Food Safety Helpline explainer](https://foodsafetyhelpline.com/fssai-issues-directions-regarding-recommended-dietary-allowances/)
- [PIB — 44th Food Authority meeting, bold nutrition labelling](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2031260)
- [Indian Nutrition Rating / FOPNL amendment analysis](https://www.freyrsolutions.com/blog/fssai-amends-labelling-display-regulation-makes-inr-indian-nutrition-rating-mandatory-on-fopnl-of-food-products)
- [DPDP Act 2023 (MeitY, PDF)](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf) · [DPDP Rules 2025 overview](https://en.wikipedia.org/wiki/Digital_Personal_Data_Protection_Rules,_2025) · [KPMG — DPDP impact on healthcare & life sciences](https://kpmg.com/in/en/insights/2025/12/the-privacy-prescription-impact-of-dpdp-act-and-rules-in-healthcare-and-life-sciences-sector.html) · [EY compliance guide](https://www.ey.com/en_in/insights/cybersecurity/decoding-the-digital-personal-data-protection-act-2023)

**International composition & method**

- [USDA FoodData Central](https://fdc.nal.usda.gov/) · [Downloadable datasets](https://fdc.nal.usda.gov/download-datasets/) · [Inventory & update log](https://fdc.nal.usda.gov/log)
- [USDA Table of Nutrient Retention Factors, Release 6 (2007)](https://agdatacommons.nal.usda.gov/articles/dataset/USDA_Table_of_Nutrient_Retention_Factors_Release_6_2007_/24660888)
- [UK CoFID — Composition of Foods Integrated Dataset](https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid)
- [FAO/INFOODS Guidelines for Food Matching v1.2](https://www.fao.org/fileadmin/templates/food_composition/documents/upload/INFOODSGuidelinesforFoodMatching_version_1_2.pdf) · [Guidelines for Checking Food Composition Data](https://www.fao.org/fileadmin/templates/food_composition/documents/upload/Guidelines_data_checking.pdf) · [Greenfield & Southgate, *Food Composition Data: Production, Management and Use*](https://www.fao.org/fileadmin/templates/food_composition/images/FCD.pdf)
- [EuroFIR Compilers' Toolbox — recipe calculation](http://toolbox.foodcomp.info/ToolBox_RecipeCalculation.asp)
- [Food Composition Table for Bangladesh (FAO-hosted)](https://www.fao.org/fileadmin/templates/food_composition/documents/FCT_10_2_14_final_version.pdf)
- [Open Food Facts — data, API, SDKs](https://world.openfoodfacts.org/data) · [API reuse conditions (ODbL)](https://support.openfoodfacts.org/help/en-gb/12-api-data-reuse/94-are-there-conditions-to-use-the-api) · [API documentation](https://openfoodfacts.github.io/openfoodfacts-server/api/)

**South Indian specifics**

- Shakappa D, Naik R, Sobhana PP. Glycemic carbohydrates, GI and GL of commonly consumed South Indian breakfast foods. J Food Sci Technol. 2022;59(9):3619–26. — [PubMed 35875218](https://pubmed.ncbi.nlm.nih.gov/35875218/) · [free full text, PMC9304465](https://pmc.ncbi.nlm.nih.gov/articles/PMC9304465/) · [doi:10.1007/s13197-022-05368-6](https://doi.org/10.1007/s13197-022-05368-6)
- [Carbohydrate profiling & glycaemic indices of selected traditional Indian foods (IJMR)](https://ijmr.org.in/carbohydrate-profiling-glycaemic-indices-of-selected-traditional-indian-foods/)
- [Starch digestibility and glycaemic index of selected Indian traditional foods](https://www.tandfonline.com/doi/full/10.1080/10942912.2017.1295387)
- [South Indian cuisine with low-GI ingredients reduces CV risk in T2DM (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7504299/)

**Language & vision**

- [AI4Bharat](https://ai4bharat.iitm.ac.in/) · [IndicXlit (GitHub)](https://github.com/AI4Bharat/IndicXlit) · [IndicXlit project page](https://indicnlp.ai4bharat.org/indic-xlit/) · [`ai4bharat-transliteration` on PyPI](https://pypi.org/project/ai4bharat-transliteration/) · [Indic NLP Catalog](https://ai4bharat.github.io/indicnlp_catalog/)
- [Benchmarking LLMs for Transliteration of Indian Languages (arXiv 2505.19851)](https://arxiv.org/pdf/2505.19851)
- [Are Vision-Language Models Ready for Dietary Assessment? (arXiv 2504.06925)](https://arxiv.org/abs/2504.06925)
- [Object Detection in Indian Food Platters with YOLOv4 (arXiv 2205.04841)](https://arxiv.org/pdf/2205.04841) · [IndianFoodNet (IIETA)](https://www.iieta.org/journals/ijcmem/paper/10.18280/ijcmem.110403) · [Roboflow Indian food datasets](https://universe.roboflow.com/indianfood/indian_food-pwzlc)
- [Comprehensive survey of image-based food recognition and volume estimation (arXiv 2106.11776)](https://arxiv.org/pdf/2106.11776)

**Regulatory — US / UK / EU / AU**

- [FDA town hall — General Wellness Policy for Low Risk Devices, final guidance (Feb 2026)](https://www.fda.gov/medical-devices/medical-devices-news-and-events/town-hall-general-wellness-policy-low-risk-devices-final-guidance-02112026)
- [Covington — FDA issues revised guidance on general wellness products (Jan 2026)](https://www.cov.com/en/news-and-insights/insights/2026/01/fda-issues-revised-guidance-on-general-wellness-products)
- [DLA Piper — FDA updates CDS and General Wellness guidances, key points](https://www.dlapiper.com/en-us/insights/publications/2026/01/fda-updates-its-clinical-decision-support-and-general-wellness-guidances-key-points)
- [Troutman Pepper Locke — FDA's 2026 general wellness guidance](https://www.troutman.com/insights/fdas-2026-guidance-on-general-wellness-devices-policy-for-low-risk-devices/)

**Competitive & portion-size**

- [HealthifyMe AI food image recognition for Indian food (TechCrunch)](https://techcrunch.com/2023/09/21/khosla-backed-healtifyme-introduces-ai-powered-image-recognition-for-indian-food/)
- [Top fitness and diet apps in India 2026 (StartupTalky)](https://startuptalky.com/top-fitness-diet-apps-india-listicle/)
- [Indian portion sizes — katori/roti/rice reference](https://www.yourtrainer.in/blog/indian-portion-sizes-katori-roti-rice-grams)
- [Food consumption patterns using katori/glass/cup standardisation (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5571590/)
- [Accuracy of hands vs household measures as portion size estimation aids (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4976119/)

---

*Pathyam (பத்தியம் / పథ్యం / പഥ്യം / ಪಥ್ಯ) — the Ayurvedic term for prescribed, wholesome diet. The name already claims the clinical position; the data has to earn it.*

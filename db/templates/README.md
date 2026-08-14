# Template Authoring

Parametric recipe templates in hand-editable YAML, validated before they can reach the
database.

```bash
python -m pathyam_engine.authoring --dir db/templates validate
python -m pathyam_engine.authoring --dir db/templates load  --dsn "$PATHYAM_DSN"
python -m pathyam_engine.authoring --dir db/templates audit --dsn "$PATHYAM_DSN"
```

**Current state:** 17 templates, 52 ingredients, 0 validation errors. **1 of 17
computes** — the other 16 are blocked on composition data, which is the expected state
until the ICMR-NIN licence lands.

## Why the audit is the point

Authoring ~400 templates is Phase 1's critical path, and the bottleneck is review
capacity, not typing. Two design consequences follow.

**The validator has to catch what a reviewer would miss.** Not typos — those surface
immediately — but the silent ones. `test_authoring.py` pins each check against the
mistake it prevents.

**The audit produces a worklist, not a score.** The question an author actually needs
answered is "what is stopping this from computing", and the answer is nearly always a
specific list of ingredients with no IFCT values loaded. Sorted by how many templates
each unblocks:

```
  ifct    ingredient                            group        blocks
  E009    Curry leaves                          vegetable         9
  S016    Mustard seeds                         spice             7
  S001    Asafoetida                            spice             5
  D008    Chilli, green                         vegetable         5
  V010    Sunflower oil                         fat               5
  ...
  38 ingredients block 16 templates.
```

That table is the extraction brief for the ICMR-NIN conversation: **38 IFCT codes
unblock the entire current library.** Twelve of them unblock two-thirds of it.

## The bug the audit found

The energy-band check flagged idli at **317 kcal/100 g** against a real value near 110.

Root cause: `batter_g` was authored as *wet batter mass* but consumed by the ingredient
expressions as dry solids — and IFCT reports composition per **dry** weight. Every
fermented-batter dish was overstated by roughly 2.6×. The bug was in the original seed
data too, and had gone unnoticed through schema verification, engine tests and an API
demo, because 386 kcal for a dosa looks plausible if you are not checking against a
reference.

Fixed by rebasing on dry solids (`solids_g`) and recomputing yields, which are
consequently well *above* 1 for anything steamed or simmered. Idli now computes at
54 kcal/serving and 121 kcal/100 g. `test_fermented_batter_templates_are_on_a_dry_solids_basis`
prevents a regression.

The energy bands are deliberately wide — they catch order-of-magnitude slips, not
borderline cases. Tightening them before real composition data lands would only
generate noise.

## Authoring format

```yaml
templates:
  - id: PY-T-000111          # PY-T-nnnnnn
    dish: dosa_plain         # key in eval/lexicon_south_indian.yaml
    method: griddled         # drives retention and yield factor lookup
    servings: 1              # one batch makes this many servings
    yield_factor: 1.79       # cooked mass / DRY raw mass
    parameters:
      solids_g: {dist: lognormal, mu: 3.43, sigma: 0.25, unit: g, observable: true}
      fat_g:    {dist: lognormal, mu: 2.08, sigma: 0.55, unit: g,
                 ask: "How much oil or ghee was used?",
                 options: [{label: little, value: 4}, {label: generous, value: 15}]}
      fat_type: {dist: categorical, categories: [gingelly, coconut, ghee, sunflower],
                 weights: [0.45, 0.30, 0.15, 0.10]}
    ingredients:
      - {food: rice_parboiled, qty: 'solids_g * rice_fraction', prep: soaked_ground}
      - {food: gingelly_oil,   qty: 'fat_g * (fat_type == "gingelly")'}
      - {sub_template: PY-T-000112, qty: 'filling_g'}   # TARGET MASS of the sub-recipe
    regional:
      KL: {fat_type: {dist: categorical, categories: [gingelly, coconut, ghee, sunflower],
                      weights: [0.10, 0.75, 0.10, 0.05]}}
```

Distributions: `point | normal | lognormal | uniform | categorical | beta`. For
lognormal, `mu`/`sigma` describe **ln(X)**, so the median is `exp(mu)` — getting this
backwards is a silent 2–3× portion error.

`observable: true` means a vision model can plausibly see the parameter. Cooking oil,
fermentation time and rice type are all `false`, which is why the app asks.

## What the validator checks

| Check | The mistake it prevents |
|---|---|
| Unknown ingredient or sub-template key | Typo silently drops an ingredient |
| Expression references undeclared parameter | Fails at request time instead of authoring time |
| Expression safety (AST whitelist) | Code execution via a database row |
| Prior payload well-formed, samples cleanly | Malformed prior becomes a production incident |
| **Categorical coverage** | **A category with prior weight that no ingredient selects — that share of samples silently contains no cooking fat** |
| Selector names a category that exists | Dead branch contributing nothing |
| Regional override keeps the same category set | Regional prior selects categories no ingredient covers |
| Regional override targets a declared parameter | Override silently ignored |
| Yield factor within 0.2–5.0 | Order-of-magnitude slip |
| Sub-template cycles | Infinite recursion at compute time |
| YAML parse errors reported with line and column | Unquoted `: ` surfacing as a Python traceback |

Categorical coverage is the one worth understanding. Add `sunflower` to a `fat_type`
prior, forget the matching ingredient row, and 10% of Monte Carlo draws contain **no
oil at all**. Nothing raises; the energy estimate is just quietly low for that share.

## Composition data is deliberately absent

`ingredients.yaml` defines identity only — name, group, IFCT code, density. No nutrient
values. Inventing plausible-looking numbers to make the pipeline run would produce a
database that computes confidently and is wrong, which is the failure this project
exists to avoid and is unrecoverable once anyone starts trusting the output.

The loader writes identity rows and templates. Composition arrives separately, from a
licensed source, carrying its own provenance and confidence tier.

## Adding a dish

1. Add any new ingredients to `ingredients.yaml` with their IFCT code.
2. Add the template to a `*.yaml` file in this directory.
3. `validate` until clean.
4. `load`, then `audit` — check the energy band and the dominant uncertainty parameter.
5. If the dominant parameter has no `ask`, add one. That is the question the app will
   put to the user, and it should be the one that collapses the most uncertainty.

## Known gaps

1. **17 of a target ~400 templates.** Coverage is tiffin, common gravies and rice
   dishes. Missing: Kerala Sadya items, Chettinad, Andhra pickles, coastal Karnataka,
   most sweets and snacks.
2. **Quantities need dietitian review.** They are model parameters from domestic
   practice, not measurements.
3. **One yield factor per template.** Real dishes hydrate unevenly — rice absorbs
   water, oil does not — so a single factor is an approximation. Acceptable while fat
   is a small mass share; revisit for oil-heavy dishes.
4. **`curd_rice` and `tamarind_rice` enter rice as cooked mass**, unlike every other
   template. Flagged in their notes so nobody "corrects" it.
5. **No link yet between `dish:` keys and `ref.food_name`.** The lexicon and the
   template library are separate files that agree by convention, not by constraint.

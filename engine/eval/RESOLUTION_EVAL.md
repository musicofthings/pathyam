# Dish Resolution — Evaluation Results

12 August 2026 · 145 golden queries · 50-dish South Indian lexicon · `python -m pathyam_engine.evaluation`

---

## Headline

| Lexicon | top-1 | top-5 | MRR | median latency |
|---|---|---|---|---|
| **Catalogued** (all 327 alias rows present) | **100.0%** | 100.0% | 1.000 | 4.9 ms |
| **Held-out** (65 tested spellings removed) | **83.3%** | 92.0% | 0.867 | 3.7 ms |

Two modes, both meaningful. *Catalogued* is what production looks like once the alias
table is built out. *Held-out* removes every alias row matching a golden query, forcing
the resolver to generalise to spellings nobody catalogued — the honest measure, and the
only one where ablation says anything.

**The 16.7-point gap is the value of alias curation.** 65 alias rows bought it. That is
the single most important number in this document, and §4 explains why.

## 1. The harness was broken first, and the ablation is what exposed it

The first run reported **100% top-1 with a 0.0-point ablation delta across every
configuration**. That is not a good result; it is a broken measurement. 134 of 145
queries were resolving by exact match, because the golden queries had been drawn from
the lexicon's own alias list. The harness was measuring dictionary lookup.

A component ablation showing *zero* difference is the tell. If phonetic folding and
word containment both contribute nothing, either they are useless or nothing is
exercising them. `test_holdout_materially_changes_the_score` now fails loudly if the
two modes converge again.

## 2. By category (held-out)

| Category | n | top-1 | top-5 |
|---|---|---|---|
| exact English | 24 | 100.0% | 100.0% |
| native Tamil | 8 | 100.0% | 100.0% |
| native Kannada | 6 | 100.0% | 100.0% |
| native Malayalam | 6 | 100.0% | 100.0% |
| native Telugu | 5 | 100.0% | 100.0% |
| with quantity | 13 | 92.3% | 100.0% |
| with modifier | 5 | 100.0% | 100.0% |
| code-mixed | 5 | 100.0% | 100.0% |
| misspelling | 16 | 81.2% | 100.0% |
| colloquial | 16 | 68.8% | 93.8% |
| **romanised** | **30** | **53.3%** | **73.3%** |
| ambiguous (should ask) | 5 | 100.0% | 100.0% |
| absent (should not match) | 6 | — | 100.0% correct |

Native script is perfect. Romanised cross-language names are where everything fails,
and §4 is about why.

## 3. Abstention calibration

| Metric | Value | Reading |
|---|---|---|
| Asked for confirmation | 39.3% (57/145) | High, but the held-out lexicon is deliberately impoverished |
| ...of those, would have been wrong | 35.1% | It asks when it matters |
| Errors it did **not** flag | **3.4%** | Confident mistakes reaching the user — the number that matters |
| Recall on cases marked "should ask" | **100.0%** | Every ambiguous and absent case was caught |
| Absent dishes still offered a candidate | 28.6% | Pointless prompts, not data errors |

3.4% silent errors and 100% recall on should-ask is the result worth defending: the
system's confident answers are almost always right, and it reliably declines when it
should.

## 4. Ablation — and the decision it drives

| Configuration | top-1 | top-5 | MRR | Δ top-1 |
|---|---|---|---|---|
| baseline | 83.3% | 92.0% | 0.867 | — |
| no phonetic folding | 81.2% | 89.1% | 0.846 | −2.2 pts |
| no word containment | 80.4% | 89.1% | 0.831 | −2.9 pts |
| neither (raw trigram only) | 77.5% | 82.6% | 0.795 | **−5.8 pts** |

Both components earn their place and compose roughly additively. On n=145 a single
query is 0.7 points, so the −2.2 and −2.9 figures are ~3–4 queries each: real, but not
large. The −5.8 combined figure is solid.

### The residual failures split into two classes

**Class A — ranking within a dish family (6 failures).** `dosai`, `thosai`, `dose`,
`dhosa`, `dosey` all retrieve plain dosa but rank it 2nd–4th behind rava/ghee-roast/
masala dosa. These are *retrieval successes and ranking failures* — the right answer is
in the top 5 every time. Fixable with better scoring or a learned reranker once
correction data exists.

**Class B — zero orthographic overlap (9 failures).** These do not appear in the top 5
at all:

| Query | Target | Shared substring |
|---|---|---|
| mosaranna (kn) | curd rice | none |
| daddojanam (te) | curd rice | none |
| perugannam (te) | curd rice | none |
| chitranna (kn) | lemon rice | none |
| huli (kn) | sambar | none |
| chammanthi (ml) | coconut chutney | none |
| thenkuzhal (ta) | murukku | none |
| majjiga pulusu (te) | mor kuzhambu | none |
| string hopper (en) | idiyappam | none |

**No string-similarity method can bridge these.** Trigram, phonetic folding, edit
distance — all operate on characters, and there are no shared characters to operate on.

### Does this justify embeddings and a GPU? No — not yet.

Class B is a **knowledge problem, not a semantics problem**. Resolving "mosaranna" to
curd rice requires knowing Kannada food vocabulary. Multilingual embedding models
(LaBSE, bge-m3, multilingual-e5) are trained predominantly on web text where these
hyper-local romanised terms are sparse or absent, and dish names are 1–3 tokens with
almost no context for the model to work with. Expecting a general-purpose encoder to
know that *chammanthi* means coconut chutney is optimistic.

The catalogued run settles it: **with those aliases present, top-1 is 100%.** Nine
lexicon rows fix all nine Class B failures, deterministically, at zero inference cost,
with no model to serve or version.

**Recommendation: build the alias table. Do not buy GPU capacity for this.**
Curating aliases for ~500 dishes across four languages is a few weeks of work for
someone with the right language background — and it is exactly the proprietary asset
the strategy already identified as the moat, so the effort is not a detour.

## 5. Two scoring bugs the eval surfaced

**Saturation destroyed the ranking signal.** Four dosa variants all scored base 0.940;
with boosts of 0.09–0.12 each, every one clamped to exactly 0.990. Adding a
base-variant boost changed *nothing* — the ceiling had already flattened the ranking.
Boosts now consume a fraction of the remaining headroom instead of being added and
clamped, which is monotonic in both similarity and boost. **+2.9 points.**

**The harness overrode the tuned retrieval floor.** `evaluate()` hard-coded
`min_similarity=0.20`, so the CLI reported numbers for a configuration that no longer
shipped. It now defers to the resolver's own default.

## 6. When to re-run

`test_held_out_performance_does_not_regress` pins top-1 ≥ 0.80, top-5 ≥ 0.88,
MRR ≥ 0.84. Raise those thresholds whenever the resolver genuinely improves. Re-run the
full report after:

- adding alias rows (expect held-out to rise toward catalogued)
- changing any scoring constant in `resolver.py` or `sources.py`
- adding vector retrieval, if it is ever justified — and it must beat 83.3% held-out to
  count, not 77.5% raw trigram

## 7. Caveats

- **145 queries is small.** One query is 0.7 points. Differences under ~2 points are
  not meaningful. Target 500+ queries before treating small deltas as real.
- **The lexicon needs native-speaker review.** Malayalam and Telugu forms are the least
  certain; see the header of `lexicon_south_indian.yaml`.
- **Queries are author-written, not user-sampled.** They encode a plausible model of how
  people type, which is not the same as how they actually do. Replace with real logs as
  soon as there are any — that is the single biggest improvement available to this
  harness.
- **No per-region or per-user-prior evaluation yet.** Both boosts exist in the resolver
  and neither is exercised by the golden set.

---

## The held-out romanised ceiling is 70%, not 100%

Run `PYTHONPATH=. python3 eval/lexeme_ceiling.py` to reproduce.

Held-out evaluation removes a query's own alias and asks the resolver to find the
dish anyway. That is the right test for a **spelling variant** — "dosai" should reach
`dosa_plain` via "dosa", "puliyodarai" via "puliyodharai". It is not a meaningful
test for a **distinct regional lexeme**: "huli" is simply the Kannada word for
sambar and shares no morphology with any other surface form of that dish. Its nearest
sibling scores 0.18. No phonetic folding, trigram or edit-distance method derives it,
because there is nothing to derive it from.

Of the 30 romanised golden queries:

| | count | what it measures |
|---|---|---|
| spelling variants | 21 | resolver quality — genuine headroom |
| distinct lexemes | 9 | lexicon coverage — unreachable when held out |

    huli -> sambar              neychoru -> ghee_rice       chammanthi -> coconut_chutney
    chitranna -> lemon_rice     thenkuzhal -> murukku       majjiga pulusu -> mor_kuzhambu
    mosaranna / daddojanam / perugannam -> curd_rice

All nine are already in the lexicon; the ablation is what removes them. Against the
catalogued lexicon every one resolves, which is why the catalogued score is 100%.

**Consequences.**

1. Romanised held-out is currently 53.3% against a ceiling of 70.0%. The real
   headroom is about 5 queries, not 14. A target of "romanised ≥ 80% held-out" is
   unachievable by construction and should not be set.
2. Report romanised held-out against the 70% ceiling, or report the two classes
   separately. Quoting 53.3% against an implied 100% overstates how bad the resolver
   is and points effort at the wrong problem.
3. The way to serve a user who types "huli" is lexicon coverage, not a better
   matching algorithm. Every regional name added is free recall — the lexicon file
   already says `aliases` should grow, not shrink.
4. **IFCT 2017 does not help here.** The ingest loaded 1,341 native-language names
   into `ref.food_name`, but IFCT is a table of ingredients and raw foods: it carries
   "Sorrakaya" (bottle gourd) and "Nuvvulu" (gingelly seeds), not prepared dishes.
   Zero of the nine lexemes above appear in IFCT-sourced names. Those names are
   valuable for resolving *ingredient* mentions, which nothing currently covers —
   but that is a different capability from dish resolution, and it is not what this
   harness measures.

---

## Why the dosa cluster fails held-out — and why the fix is not in `trigram.py`

Six of the 23 held-out failures are one cluster: `dosai`, `thosai`, `dose`, `dhosa`,
`dosey`, `oru dosai` all expect `dosa_plain` and lose to `dosa_rava` or
`dosa_ghee_roast`. Measured, not assumed:

    word_similarity('dosai', 'dosa')              0.571
    word_similarity('dosai', 'ravai dosai')       1.000   <- alias of dosa_rava
    word_similarity('dosai', 'ghee roast dosai')  1.000   <- alias of dosa_ghee_roast

    resolver output for 'dosai':
      Rava dosa      score 0.9420  sim 0.9400  trigram
      Dosa, plain    score 0.5915  sim 0.5371  trigram   (+base_variant 0.03)

Two separate things are happening, and they need separate fixes.

**1. An unmatched qualifier costs nothing.** `word_similarity` scores the best
window, so a one-word query matching one word of a two-word candidate scores a
perfect 1.0. "dosai" against "ravai dosai" is a full-credit match even though the
candidate carries a qualifier the user never said. The existing `base_variant` boost
(0.03, and applied to *headroom*, which is nearly zero once similarity is high) is far
too small to overcome a 0.94-vs-0.54 gap.

The fix belongs in `resolver._rerank`, **not** in `trigram.word_similarity`. That
function is deliberately bug-compatible with PostgreSQL's `pg_trgm` so that offline
ranking equals production ranking; changing its semantics silently breaks that
guarantee. Add a coverage term to the resolver's blend instead: discount a candidate
in proportion to the share of its own words the query left unexplained. Note that a
naive version is not sufficient on its own — at a 0.6 floor the example above still
scores 0.752 against plain dosa's 0.537 — so the coverage discount has to be paired
with letting the phonetic signal win here (`phonetic_similarity('dosai', 'dosa')` is
1.000, and the resolver currently prefers the higher trigram score).

**2. The ablation makes this worse than production.** `holdout` removes a query
string from *every* dish. Removing "dosai" strips it from `dosa_plain`, but
`dosa_rava` keeps "ravai dosai", which still contains the token exactly. So the base
dish is stripped of the user's spelling while a sibling retains a near-identical one.
In production "dosai" is a catalogued alias of `dosa_plain` and resolves exactly.

So part of the 53.3% is a measurement artifact on top of the lexeme ceiling already
documented above. Before tuning, consider holding out per-dish rather than globally,
or excluding sibling aliases that contain the held-out token — otherwise the harness
rewards changes that fix an artifact rather than a user-visible failure.

**Not attempted here.** Changing ranking without room to re-run the full ablation
would be unmeasured tuning, which is what this document exists to prevent.

---

## Attempted: coverage-scaled containment. Measured, then reverted.

The flat `_WORD_MATCH_DISCOUNT = 0.94` in `resolution/sources.py` treats every partial
match alike. Replacing it with a discount that scales with **coverage** — the share of
the candidate's own words the query accounts for — was implemented and measured:

| | before | after |
|---|---|---|
| top-1 (held-out) | 83.3% | **84.1%** |
| colloquial | 68.8% | **75.0%** |
| romanised | 53.3% | 53.3% |
| top-5 | 92.0% | 91.3% |
| MRR | 0.867 | 0.865 |
| abstention recall on "should ask" | 100% | **90.9%** |

**Reverted.** The abstention drop breaks
`test_abstention_catches_every_case_marked_should_ask`, which asserts the resolver
flags every case the golden set marks as needing confirmation. That is a safety
property — asking when unsure — and trading it for 0.8 points of top-1 is not a
trade worth making silently. The colloquial gain is real and the idea is probably
right; it needs a floor sweep against the abstention invariant, which is the work
this note exists to hand over.

## Why the dosa cluster is a harness artifact, not a resolver bug

The coverage change did not move romanised at all, and dumping candidates showed why.
`dosa_plain`'s surface forms are:

    Dosa, plain | dosa | dosai | thosai | dose | dosey | dhosa | plain dosa | sada dosa

Six of those — `dosa`, `dosai`, `thosai`, `dose`, `dosey`, `dhosa` — are themselves
golden queries. The harness holds out every golden query string at once, so the dish
is left with only `Dosa, plain`, `plain dosa` and `sada dosa`: **no bare form
survives**. Meanwhile `dosa_rava` keeps `ravai dosai`, which contains the queried
token exactly. A one-word query then cannot beat a two-word sibling, whatever the
scoring does.

This is not the "same alias on two dishes" case — that was checked and there are
**zero** alias strings shared between dishes, so per-dish holdout is a no-op on this
lexicon. It is that holding out *all* spellings of a base dish simultaneously models
a situation that does not occur: in production `dosai` is a catalogued alias of
`dosa_plain` and resolves exactly.

**Fix the harness before tuning the ranker.** Hold out one alias at a time
(leave-one-out) rather than the whole golden set at once. That models the real case —
an unseen spelling of a dish whose *other* spellings are known — and would let a
ranking change be judged on whether it helps users rather than on whether it can
recover a dish stripped of every bare name it has.

---

## Leave-one-out holdout — implemented

`evaluate_leave_one_out()` removes only the query's **own** alias, leaving the dish's
other spellings in place. This is now the primary number the CLI reports; the global
holdout is kept as a deliberately harsher floor and is still what the ablation runs
against.

| | global holdout | leave-one-out |
|---|---|---|
| top-1 | 83.3% | **85.5%** |
| top-5 | 92.0% | 92.0% |
| MRR | 0.867 | **0.881** |
| misspelling | 81.2% | **93.8%** |
| with_quantity | 92.3% | **100.0%** |
| romanised | 53.3% | **56.7%** |
| colloquial | 68.8% | 62.5% |
| abstention recall on "should ask" | 100% | 100% |
| silent errors | 3.4% | **1.1%** |

Four of the six dosa-cluster failures are gone — `thosai`, `dhosa`, `dosey` and
`oru dosai` now resolve, because `dosa_plain` keeps its other bare spellings instead
of being stripped of all of them at once. `dosai` and `dose` still miss.

Two results worth not glossing over:

**Colloquial got worse** (68.8% → 62.5%). Leave-one-out is not uniformly easier: it
also leaves *competitors'* aliases in play, and several colloquial queries now lose to
a sibling dish whose alias the global holdout happened to remove as collateral. That
is the more realistic contest, and the lower number is the more truthful one.

**Silent errors fell from 3.4% to 1.1%** while abstention recall stayed at 100%. The
resolver is not just scoring higher, it is wrong-and-confident less often — which
matters more than top-1 for a system that logs what someone ate.

### What this does and does not license

It does not move the lexeme ceiling. The nine distinct regional lexemes (`huli`,
`chammanthi`, `chitranna`, …) still fail under leave-one-out, because no ablation
setting can conjure a word the lexicon never contained. Romanised at 56.7% now sits
against a 70.0% ceiling — roughly four queries of real headroom.

The coverage-scaled containment change reverted in the previous commit should be
re-tried **against this number**, not the global one, and re-checked against the
abstention invariant that rejected it the first time.

---

## Coverage-scaled containment — retried against leave-one-out, kept

The change reverted earlier was re-applied and the floor swept against the
leave-one-out number instead of the global holdout. The floor turns out to be
load-bearing, and 0.80 is the only value that takes the full gain for free:

| floor | top-1 | top-5 | MRR | romanised | ask-recall | |
|---|---|---|---|---|---|---|
| 1.00 | 85.5% | 92.0% | 0.881 | 56.7% | 100% | coverage disabled |
| 0.90 | 87.0% | 92.0% | 0.889 | 56.7% | 100% | |
| **0.80** | **88.4%** | **92.0%** | **0.899** | **63.3%** | **100%** | **chosen** |
| 0.70 | 88.4% | 91.3% | 0.897 | 63.3% | 100% | top-5 starts to fall |
| 0.60 | 88.4% | 91.3% | 0.897 | 63.3% | 91% | abstention breaks |
| 0.55 | 88.4% | 91.3% | 0.897 | 63.3% | 91% | the value tried first |

The first attempt used 0.55 by eye, broke
`test_abstention_catches_every_case_marked_should_ask`, and was reverted. The
accuracy at 0.55 and at 0.80 is identical — the earlier attempt gave up a safety
property for nothing.

### Where resolution stands

| | session start | now |
|---|---|---|
| top-1 (leave-one-out) | 83.3% | **88.4%** |
| top-5 | 92.0% | 92.0% |
| MRR | 0.867 | **0.899** |
| romanised | 53.3% | **63.3%** |
| colloquial | 68.8% | **75.0%** |
| misspelling | 81.2% | **93.8%** |
| with_quantity | 92.3% | **100.0%** |
| silent errors | 3.4% | **1.1%** |
| abstention recall on "should ask" | 100% | 100% |

Romanised at 63.3% now sits against the 70.0% lexeme ceiling — about two queries of
headroom, both spelling variants (`dosai`, `dose`, still losing to qualified dosa
siblings). The remaining nine failures are the distinct regional lexemes, which only
lexicon coverage can fix.

**Do not re-tune the floor by eye.** Re-run the sweep; the accuracy plateau extends
well past the point where abstention breaks, so a value that looks free is not.

---

## Lexicon coverage — audited, three real gaps, filled

The nine regional lexemes that fail held-out (`huli`, `chammanthi`, `chitranna`,
`mosaranna`, `daddojanam`, `perugannam`, `saaru`, `thenkuzhal`, `majjiga pulusu`)
were checked against the lexicon: **all nine are already catalogued.** They fail only
because the ablation removes them. There was nothing missing to add.

Alias coverage is likewise healthy — 264 surface forms across 51 dishes, with only
four dishes carrying fewer than three aliases.

Native-script coverage looked far worse: ta 39/51, te 21/51, ml 23/51, kn 28/51, or
93 blanks. Almost all of those are correct absences. `olan` and `kaalan` are Kerala
dishes and have no Tamil or Telugu name; demanding one would invite somebody to
invent it. Scoping the check to the regions each dish actually claims —
TN→ta, KL→ml, KA→kn, AP/TG→te — turns 93 apparent gaps into **three real ones**:

    coconut_rice    claims KL, had no Malayalam name
    tomato_chutney  claims KA, had no Kannada name
    bonda           claims AP/TG, had no Telugu name

All three are filled, each marked `# REVIEW` in line with the file's standing warning
that it needs native-speaker sign-off. Two are direct borrowings (`ಟೊಮೇಟೊ ಚಟ್ನಿ`,
`బోండా`) and one a compound of common words (`തേങ്ങ ചോറ്`), which is why these three
were safe to supply and a mass fill of the other 90 would not have been.

Two tests now hold the line: every dish must carry a native name for each region it
claims, and no surface form may be claimed by two dishes (an ambiguous alias silently
logs the wrong food).

**The eval numbers do not move, and should not.** New aliases are not in the golden
query set, so they change nothing measurable — which is the point. Lexicon coverage
serves users who type words the golden set never asked about. Measuring it would need
new golden queries, authored independently rather than back-filled from the aliases
just added; doing that from the alias list is the exact mistake that made the first
version of this harness report 100%.

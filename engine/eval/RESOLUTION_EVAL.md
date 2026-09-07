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

# Session Handover
_Generated: 2026-09-07T18:24:04Z_
_Branch: phase1/restore-trust_
_Trigger: usage threshold 89% | Context at compact: n/a_
_Compact count this project: 0_

---

## 🎯 Active Task
**What we're building/fixing:**
Replacing every synthetic/placeholder artefact in Pathyam with real data and working
code, following a six-phase plan derived from a full repo review. Phase 1 (remove
fabrications, fix the broken schema) is done and pushed. Phase 2 (real data spine) is
partly done: the authoritative ICMR-NIN IFCT 2017 table is ingested and a real engine
bug it exposed is fixed.

**Phase:** Phase 2 — Real data spine (in progress)
**Next action:** Two items, both explicitly requested by the user:
(1) persist the meal journal to Postgres, retiring the in-memory `_JOURNAL_STORE` in
`engine/pathyam_api/main.py` (~line 515) onto `app.meal_log` from
`db/007_meal_logs_inference.sql`, adding a user identifier;
(2) supply composition for the 5 ingredients that have no IFCT 2017 entry —
rice_flour, curd, sugar (refined), coconut_milk_1, coconut_milk_2 — using the
schema's existing `is_borrowed` + `borrowed_from_food_id` mechanism (tier C, enforced
by `composition_borrowed_ck`) for borrowed values, and definitional values for sucrose
and NaCl. That unblocks the last 3 templates (appam, puttu, curd_rice) → 17/17.

---

## ✅ Completed This Session

### Review (published)
- [x] Full repo review at `16a9c74`; 18 findings tiered 0/1/2.
- [x] Plan document published as an Artifact:
      https://claude.ai/code/artifact/38ff1bd1-b3e1-4d0b-96db-edf7a875673b
      (updated once to mark Phase 1 complete; re-read it with Artifact `action: "read"`
      before editing — the local scratchpad copy is wiped on session resume).

### Phase 1 — Restore trust (commit `7afa5a1`, pushed)
- [x] Fixed `ti.ingredient_id` → `ti.template_ingredient_id` in `db/012` and
      `db/full_master_schema_seed.sql`. Schema had not applied; the whole
      integration/API suite (59 tests) had never run.
- [x] Removed fabricated citation EVIDENCE_004 (cited PMID 31234567, which is a real
      PMID belonging to a paper on electrical impedance tomography of granular
      material) and removed it from the validator's hardcoded allowlist.
- [x] Deleted `authoring/full_ifct_dataset.py` + `tests/test_full_universe.py` —
      528 generated placeholder foods with values from `fid % 15` arithmetic, all
      stamped `source=IFCT2017, confidence=A`. Dead code outside its own test.
- [x] Rewrote `evaluation/benchmarks.py`: every field computed or `None`. Removed
      hardcoded `citation_recall=0.92`, `mean_latency_ms=145.0`, fake `prompt_hash`,
      and the empty-input case returning precision/recall of 1.0.
- [x] Replaced the fabricated `/v1/news/rss` (4 invented articles in 2 nonexistent
      journals) with a live NCBI E-utilities query; added batch esummary/efetch to
      `NCBIClient` with optional `NCBI_API_KEY`. Outage → empty feed, guarded by test.
- [x] Added CORS middleware (`ALLOWED_ORIGINS`, no wildcard fallback), `.env.example`,
      `.env` gitignored.
- [x] Rewrote README against what the code does, with a "Known gaps" table.

### Phase 2 — Real data spine (commit `b63e480`, pushed)
- [x] `engine/scripts/fetch_ifct.py` — fetches `@ifct2017/compositions` v2.0.9 +
      `@ifct2017/columns` v2.0.13 into gitignored `engine/.ifctdata/`. Deliberately
      NOT vendored: ICMR copyright, free-to-read only (dossier risk R1).
- [x] `engine/pathyam_engine/authoring/ifct2017.py` — ingests 542 foods, 41
      INFOODS-tagged nutrients, 19,999 values with per-value SDs, 1,341 native-language
      names (ta/te/ml/kn). Units verified empirically before mapping (guava vit C
      0.214 g→214 mg; egg cholesterol 0.366 g→366 mg; rice 1471 kJ→351.6 kcal).
- [x] `is_commercial_cleared` stays FALSE (user decision). Fixed `ifct_data.py` which
      claimed True and would have disarmed the `ref.v_uncleared_values` release gate.
- [x] Corrected 42 of 52 wrong IFCT codes in `db/templates/ingredients.yaml`; removed
      4 that have no IFCT entry. (48/52 were fabricated: black pepper claimed `S015`,
      nonexistent; real is `G031`.)
- [x] `loader.py` and `audit.py` now match foods on `ifct_code`, not authored English
      name (which deliberately differs from the IFCT name).
- [x] **Engine bug fixed: water is now conserved across cooking.** The yield factor
      changed the mass denominator without adding the water that caused it. Idli read
      3.4 g water/100 g (real ~68); proximates summed to 35 g instead of 100.
      Now: idli 68.1 g water, proximates 99.7; dosa 99.2; ven pongal 99.5.
      A yield removing more water than the ingredients contain now warns.
- [x] `ifct_data.py` relabelled as a test fixture, not a composition source of truth.
- [x] Added `tests/test_ifct2017.py` (fixture-based, does not need the licensed CSV)
      and water-balance tests in `tests/test_engine.py`.

**Template coverage: 6% (1/17) → 82% (14/17). Tests: 207 → 278 passing.**

---

## 🔄 In Progress (Exact Resume Point)
**Branch:** `phase1/restore-trust` (contains both Phase 1 and Phase 2 commits)
**Last commit:** `b63e480 feat: ingest the real IFCT 2017 tables; conserve water across cooking`
**Working tree:** clean, pushed to origin
**Next immediate action:** Start the two items under "Active Task → Next action".
Nothing is half-edited; resume cleanly.

---

## 📋 Remaining Work

### Immediate (user asked for both, this is the current task)
1. **Persist the meal journal.** `_JOURNAL_STORE` in `engine/pathyam_api/main.py` is a
   module-level list: history, portion edits and the dashboard are lost on restart and
   shared across all callers. Target `app.meal_log` in `db/007_meal_logs_inference.sql`.
   Introduce a user identifier. Endpoints: `/v1/log`, `/v1/history`,
   `DELETE /v1/history/{id}`, `PATCH /v1/history/{id}/portion`, `/v1/dashboard/summary`.
2. **The 5 ingredients with no IFCT entry** → unblocks appam, puttu, curd_rice (17/17):
   - `rice_flour` — borrow from A015 (Rice, raw, milled), tier C, `is_borrowed=true`
   - `curd` — borrow from L002 (Milk, whole, Cow), tier C
   - `sugar` — sucrose is definitional (~100 g carbohydrate, ~398 kcal), tier B
   - `salt` — NaCl stoichiometry: Na = 22.99/58.44 = 39.34 g/100 g, tier B
   - `coconut_milk_1` / `coconut_milk_2` — extracts; compute from H007 (Coconut,
     kernel, fresh) + water rather than looking up
   Note `composition_borrowed_ck` enforces borrowed ⇒ confidence C or D.

### Then (rest of the "replace all synthetic content" instruction)
3. **Vision** (`vision/gemini_provider.py`): model id `gemini-3.7-flash` is not a real
   Google model; failures are swallowed by `except Exception: pass` and fall through to
   a hardcoded dosa+sambar mock indistinguishable from a real reading. Fail loudly,
   gate the mock behind `PATHYAM_MOCK_VISION` only, use `client.aio` for async.
4. **`/v1/perception/analyze`** — ignores the uploaded image, defaults to
   "masala dosa", returns fixed bbox/confidence 0.88. Route to real vision or 501.
5. **Evidence corpus** — 3 hardcoded documents; `search_semantic` just calls
   `search_lexical` so RRF fuses two identical rankings. No pgvector, no FTS.
6. **Citation validator** — checks identifier existence only, never that the record
   matches the claim. Needs title/author/year agreement and a three-state
   VERIFIED / UNVERIFIED / CONTRADICTED result.
7. **CGT `predict_spike`** — coefficients (1.8, −0.02, −0.04) unsourced. Cite or
   relabel as an illustrative simulation in API docs and UI.
8. **`/v1/cgt/telemetry`** — echoes input, stores nothing.
9. **Phase 3** — romanised resolution is 53.3% top-1 (largest category). The 1,341
   IFCT native-language names now loaded into `ref.food_name` are a direct lever.
10. Mobile app: hardcoded `http://localhost:8000`, no lockfile, never built.

---

## 🏗 Architecture Decisions Made
| Decision | Rationale | Date |
|----------|-----------|------|
| Fetch IFCT data, never vendor it | ICMR-NIN copyright; free-to-read ≠ free to redistribute (dossier R1). `scripts/fetch_ifct.py` → gitignored `.ifctdata/` | 2026-09-07 |
| `is_commercial_cleared = false`, always | User decision. Keeps `ref.v_uncleared_values` working as the release gate until written ICMR-NIN permission exists | 2026-09-07 |
| Match foods on `ifct_code`, not name | Authored names deliberately differ from IFCT names ("Chilli, green" vs "Chillies, green - all varieties"); name matching silently reported missing composition | 2026-09-07 |
| A source `0` is "not reported" unless zero is physically real | The source cannot distinguish the two; a false zero in a clinical database is worse than an absent value the coverage gate reports | 2026-09-07 |
| Derived energy is tier B with `analytical_method='calculated'` | IFCT reports no energy for pure fats; Atwater over reported macros is exact but is not an analysed value | 2026-09-07 |
| Water is conserved across cooking | Yield changes mass; that mass IS water. Every other nutrient dilutes correctly, water's absolute amount also changes | 2026-09-07 |
| Delete fabricated data rather than repair it | A claim written first with a citation attached afterwards is the exact failure the evidence layer exists to prevent | 2026-09-07 |
| `ifct_data.py` demoted to test fixture | Its values are close but not the published ones (rice A014: 351.6/77.16/3.74 vs its 346/74.80/2.81) | 2026-09-07 |

---

## 🔧 Commands to Resume
```bash
# On any machine after git pull:
git pull origin phase1/restore-trust
bash scripts/session_sync.sh --load

# Rebuild the database with real IFCT data (fetches ~1.1 MB, gitignored):
cd engine && ./dev.sh --reset --no-serve

# Verify:
./run_tests.sh                                    # expect 278 passing
export PATHYAM_DSN="$(cat engine/.devdata/dsn)"
cd engine && PYTHONPATH=. python3 -m pathyam_engine.authoring \
    --dir ../db/templates audit --worklist 10     # expect 14/17, 82%
PYTHONPATH=. python3 -m pathyam_engine.evaluation # resolution eval, top-1 83.3%

# In Claude Code:
# /context-health     — verify hooks are wired
# /handover           — review this file
```

---

## 📁 Files Modified This Session
| File | Status |
|------|--------|
| db/012_recipe_compiler_state_machine.sql | modified (column fix) |
| db/full_master_schema_seed.sql | modified (column fix) |
| db/templates/ingredients.yaml | modified (42 IFCT codes corrected, 4 removed) |
| engine/scripts/fetch_ifct.py | added |
| engine/pathyam_engine/authoring/ifct2017.py | added |
| engine/pathyam_engine/authoring/ifct_data.py | modified (licence flag, demoted to fixture) |
| engine/pathyam_engine/authoring/loader.py | modified (match on ifct_code) |
| engine/pathyam_engine/authoring/audit.py | modified (match on ifct_code) |
| engine/pathyam_engine/authoring/__main__.py | modified (`ifct` subcommand) |
| engine/pathyam_engine/authoring/full_ifct_dataset.py | DELETED (synthetic) |
| engine/pathyam_engine/engine.py | modified (water conservation + warning) |
| engine/pathyam_engine/repository.py | modified (ifct_code in food_meta) |
| engine/pathyam_engine/evaluation/benchmarks.py | rewritten (no hardcoded metrics) |
| engine/pathyam_engine/evidence/hybrid_retrieval.py | modified (EVIDENCE_004 removed) |
| engine/pathyam_engine/evidence/citation_validator.py | modified (honest docstring) |
| engine/pathyam_engine/evidence/ncbi_client.py | modified (batch esummary/efetch, API key) |
| engine/pathyam_api/main.py | modified (CORS, real PubMed feed) |
| engine/pathyam_api/static/index.html | modified (empty-state for news) |
| engine/dev.sh | modified (fetch + ingest IFCT before templates) |
| engine/tests/test_ifct2017.py | added |
| engine/tests/test_engine.py | modified (water balance tests) |
| engine/tests/test_api.py | modified (news feed tests) |
| engine/tests/test_eval_benchmarks.py | modified |
| engine/tests/test_full_universe.py | DELETED |
| README.md | rewritten |
| .env.example | added |
| .gitignore | modified (.env, .ifctdata/, session state) |

---

## 🌿 Git Context
```
Branch  : phase1/restore-trust
Commit  : b63e480 feat: ingest the real IFCT 2017 tables; conserve water across cooking
Status  : clean, pushed to origin
```

Recent commits:
```
b63e480 feat: ingest the real IFCT 2017 tables; conserve water across cooking
7afa5a1 fix: repair broken schema, remove fabricated data, make docs match code
16a9c74 feat: Recipe compiler, Gemini 3.7 Flash vision, Evidence engine, 5th layer benchmarks, and full IFCT dataset
1e940d2 docs: add Google Stitch UI prompts and interactive prototype spec
612f348 feat: Gen-Z Light Pastel UI, 6 navigation tabs, CGT simulation, Recipe Catalog, RSS News feed, and cross-platform bundling
```

---

## ⚠️ Critical Rules
- Never commit secrets or API keys.
- **Never commit the IFCT CSV.** ICMR-NIN copyright; `.ifctdata/` is gitignored and
  `scripts/fetch_ifct.py` re-fetches it.
- **Never set `is_commercial_cleared = true` on IFCT2017** without written ICMR-NIN
  permission — it is the `ref.v_uncleared_values` release gate.
- Do not invent nutrient values, PMIDs, IFCT codes or metrics to make something pass.
  Absent data that the QC coverage gate reports is the correct outcome.
- Use `git add <paths>`, not `git add -A` — the latter swept session tooling state
  into a commit this session (backed out; now gitignored).
- Run /handover before switching devices.

---

## 🧬 Bioinformatics Context (if applicable)
- Not applicable. This is clinical nutrition: FAO/INFOODS tagnames, ICMR-NIN IFCT 2017
  composition, FAO/INFOODS QC gates. Do not invent local nutrient codes —
  `ref.nutrient` forbids it and retrofitting identifiers is a rewrite, not a migration.

---
_Auto-updated by `pre-compact.sh` hook and `/handover` skill._
_Read this at the start of every session. Update with `/handover`._

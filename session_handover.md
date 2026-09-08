# Session Handover
_Generated: 2026-09-08T06:00:00Z_
_Branch: main_
_Trigger: usage threshold 98% (296/300 min) | Context at compact: n/a_
_Compact count this project: 0_

---

## 🎯 Active Task
**What we're building/fixing:**
Continuing the "restore trust" programme: replacing fabricated artefacts with real,
tested code. This session moved vision from Gemini to OpenRouter with automatic
model selection, added a research-cohort schema with a working release gate, rewrote
the mobile client, wired third-party vision consent, fixed a dashboard card that
fabricated a glucose reading, and shipped password reset.

**Phase:** Post-Phase-6 follow-up. Items 10, 14, 15 done; 1 unblocked; 5 half-done.
**Next action:** Nothing is half-edited. Pick from "Remaining Work" below.

---

## ✅ Completed This Session
- [x] `8fd2d47` Pool moved from module global to `app.state`, lifespans refcounted.
- [x] `7c19b3b` Vision → OpenRouter. No default model id; catalogue is checkable
      without an API key. Removed `MealObservation.model_version` default
      `"gemini-3.7-flash"`.
- [x] `a19c31e` CGMacros dev fixture + `research` schema. **Found the release gate
      did not cover research data** — `ref.v_uncleared_values` joins only
      `ref.composition_value`. Added `ref.v_release_blockers` unioning both.
- [x] `81da44a` Mobile client rewritten. **It fabricated all four core claims**,
      including computing calories client-side as
      `label.includes('rice') ? 130 : 65`. HealthKit/Health Connect glucose sync.
- [x] `b1e2c88` Automatic model selection, free-first. Real-catalogue traps caught:
      Lyria music models priced at zero; `openrouter/auto` at −1/token; free models
      with expiry dates; only 2 of 10 free vision models support structured outputs.
- [x] `0ae23d1` `vision_third_party` consent. **Neither vision endpoint required
      auth at all.** Six tests where there had been none.
- [x] `53e58ce` Dashboard card fabricated a sensor reading — hardcoded
      "Patch Connected", `default=95.0`, and the illustrative curve shown as
      measured. Now reads `app.cgm_reading` or shows nothing.
- [x] `7e54a0e` Password reset + `pathyam_api/mailer.py`. Console mailer refused in
      production. Revokes all sessions, clears lockout, single-use 1h tokens.
- [x] Merged to `main` and pushed (was 33 commits behind, on the fabricated commit).
- [x] Ran full evals. See "Eval baseline" below.

---

## 🔄 In Progress (Exact Resume Point)
**Branch:** `main` (and `phase1/restore-trust`, both at the same commit)
**Last commit:** `e73ade2 docs: SMTP configuration and the reset entry in FOLLOW_UP`
**Working tree:** clean, pushed to origin
**Next immediate action:** Nothing half-done. Choose from Remaining Work.

### Eval baseline (2026-09-08, 430 tests passing)
```
resolution   catalogued 100.0% · leave-one-out 88.4% (top-5 92.0%, MRR 0.899)
             global holdout 84.1% · median 3.22 ms/query
ablation     no phonetic -2.9 · no containment -3.6 · neither -6.5 pts
abstention   1.1% unflagged errors · 100% recall on "should ask"
romanised    63.3% against a 70.0% ceiling (9 lexemes unreachable by design)
templates    17/17 computable, 100%
release gate 1 blocker: IFCT2017, 19,826 values
confidence   A 19,812 · B 59 · C 77
QC warnings  sambar_podi energy_atwater; sambar/rasam yield_plausible (pre-existing)
benchmarks   SafetyBenchmarkSuite: 0 golden meals, run() -> None (correct — item 6)
vision       248 image-capable, 3 free+usable; auto:free -> openrouter/free
```

---

## 📋 Remaining Work
1. **Set `OPENROUTER_API_KEY`** — the only thing between here and a real vision call.
   `PATHYAM_VISION_MODEL=auto:free` already picks a model. Read the free-tier
   privacy warning in `.env.example` first.
2. **Set `SMTP_HOST`** before real users, or reset mail goes to the log.
3. **Item 5 (yours):** the CGT tab still shows the illustrative curve, correctly
   labelled. Keep it, hide it, or drop it.
4. **Mobile photo screen** — `apiClient.resolveMealVision` is wired; no screen calls
   it. Token persistence too (session is lost on restart). Never built; no lockfile.
5. **Email verification at sign-up** — the other half of item 10.
6. **Item 13, romanised resolution:** only `saaru` and `uppittu` are genuinely
   reachable. `pulihora` is a holdout artifact, not a bug. Fixing 2 of 145 risks
   overfitting; recommend leaving it.
7. **Items 3/4/6/7/8** unchanged — need people and data, not code.

---

## 🏗 Architecture Decisions Made
| Decision | Rationale | Date |
|----------|-----------|------|
| Vision via OpenRouter, not a single vendor | The catalogue is public, so a model id is checkable before a key exists — the fabricated `gemini-3.7-flash` could not have survived it | 2026-09-08 |
| `auto` selects from the catalogue; unset still raises | Auto-selection is not a default. The catalogue cannot yield a nonexistent, blind, or non-JSON model; a hardcoded string can | 2026-09-08 |
| Exclude variable-priced and audio-output models from selection | `openrouter/auto` is −1/token and sorts first on cheapest; Lyria music models are free and "image-capable" | 2026-09-08 |
| Research cohorts in their own `research` schema | Participants consented to a different study; loading them as app users forges consent. NonCommercial data must stay separable at release | 2026-09-08 |
| One release gate (`ref.v_release_blockers`), not two canaries | A gate that a new asset class forgets to join is exactly how research data went uncovered | 2026-09-08 |
| `population_note` NOT NULL on a dataset | Glycaemic response does not transfer across populations; the disqualifying fact must travel with the data, not live in a commit message | 2026-09-08 |
| Report `provider_may_train_on_input` per response | Which model runs is an operator setting; a user cannot consent to a fact withheld from them | 2026-09-08 |
| Dashboard shows measured glucose or nothing | A prediction displayed as a sensor reading is worse than no reading. No fallback value | 2026-09-08 |
| Reset revokes every session and clears the lockout | A reset usually means someone else got in; and a lockout you cannot escape is a denial-of-service | 2026-09-08 |
| Console mailer refused in production | A reset token in a log reaches an operator, not the user — account takeover for anyone with log access | 2026-09-08 |

---

## 🔧 Commands to Resume
```bash
git pull origin main
cd engine && ./dev.sh --reset --no-serve      # --reset needed: dev_setup skips
                                              # migrations when a schema exists
./run_tests.sh                                 # 430 passing

export PATHYAM_DSN="$(cat .devdata/dsn)"
PYTHONPATH=. python3 -m pathyam_engine.evaluation                            # 88.4%
PYTHONPATH=. python3 -m pathyam_engine.authoring --dir ../db/templates audit # 17/17
PYTHONPATH=. python3 -m pathyam_engine.vision --list                         # no key needed
psql "$PATHYAM_DSN" -c "SELECT * FROM ref.v_release_blockers;"               # the gate
```

---

## 📁 Files Modified This Session
| File | Status |
|------|--------|
| engine/pathyam_engine/vision/openrouter_provider.py | added (replaces gemini_provider.py) |
| engine/pathyam_engine/vision/__main__.py | added (catalogue CLI) |
| engine/pathyam_engine/vision/protocol.py | modified (model_version required) |
| engine/pathyam_engine/authoring/cgmacros.py | added |
| engine/scripts/fetch_cgmacros.py | added |
| db/018_research_cohorts.sql | added |
| db/019_vision_consent.sql | added |
| db/020_password_reset.sql | added |
| engine/pathyam_api/mailer.py | added |
| engine/pathyam_api/auth.py | modified (reset, _mint_session, _validate_password) |
| engine/pathyam_api/main.py | modified (pool, consent, dashboard, reset, no-store) |
| engine/pathyam_api/journal.py | modified (daily_measured_peak) |
| engine/pathyam_api/schemas.py | modified |
| engine/pathyam_api/static/index.html | modified (glucose card rewritten) |
| apps/mobile/App.tsx | rewritten (fabrications removed, light theme) |
| apps/mobile/services/{apiClient,config,glucose}.ts | added/rewritten |
| apps/mobile/README.md | added |
| engine/tests/{test_vision,test_api,test_cgmacros,test_integration_postgres}.py | modified/added |
| .env.example, FOLLOW_UP.md, .gitignore, .claude/launch.json | modified |

---

## 🌿 Git Context
```
Branch  : main
Commit  : e73ade2 docs: SMTP configuration and the reset entry in FOLLOW_UP
Status  : clean, pushed to origin (main and phase1/restore-trust in sync)
```

Recent commits:
```
e73ade2 docs: SMTP configuration and the reset entry in FOLLOW_UP
7e54a0e feat: password reset — an account is no longer lost with its password
2ed5dfd docs: record that the dashboard half of item 5 is fixed
53e58ce fix: the dashboard reported a glucose reading nobody measured
0ae23d1 feat: require consent before a meal photo leaves for a third party
```

---

## ⚠️ Critical Rules
- Never commit secrets or API keys.
- **Never commit the IFCT CSV or the CGMacros data.** Both are fetched, gitignored.
- **Never set `is_commercial_cleared = true`** without written permission. Check
  `ref.v_release_blockers` — it now covers composition AND research cohorts.
- Do not invent nutrient values, PMIDs, IFCT codes, model ids or metrics. Absent
  data reported as absent is the correct outcome.
- **The repo is PUBLIC** (github.com/musicofthings/pathyam) and `main` is the
  default branch.
- Use `git add <paths>`, not `git add -A`.
- `dev.sh` skips migrations when a schema exists — use `--reset` for new ones.

---

## 🧬 Bioinformatics Context (if applicable)
- Not applicable. Clinical nutrition: FAO/INFOODS tagnames, ICMR-NIN IFCT 2017,
  FAO/INFOODS QC gates. Do not invent local nutrient codes.

---
_Auto-updated by `pre-compact.sh` hook and `/handover` skill._
_Read this at the start of every session. Update with `/handover`._

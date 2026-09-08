# Session Handover
_Generated: 2026-09-08T16:30:00Z_
_Branch: main_
_Trigger: user request — session wrap-up | Context at compact: n/a_
_Compact count this project: 0_

---

## 🎯 Active Task
**What we're building/fixing:**
Continuing the "restore trust" programme: replacing fabricated artefacts with real,
tested code. This session moved vision from Gemini to OpenRouter with automatic
model selection, added a research-cohort schema with a working release gate, rewrote
the mobile client, wired third-party vision consent, fixed a dashboard card that
fabricated a glucose reading, and shipped password reset.

**Phase:** Post-Phase-6 follow-up. Items 1, 10, 14, 15 done; 5 half-done.
**Next action:** **Billing goes active tomorrow.** Then run the live vision test —
everything else is in place:

```bash
cd engine
PATHYAM_PGDATA="$HOME/.pathyam/pg" PYTHONPATH=. \
  PATHYAM_VISION_MODEL=google/gemini-3.7-flash \
  .venv/bin/python scripts/dev_serve.py 8000
```

Then POST an image to `/v1/vision/resolve` with a bearer token and the
`vision_third_party` consent granted. **Use a photograph of a real South Indian
meal** — every image tested so far was synthetic, so accuracy is still unmeasured.

---

## ✅ Completed This Session

Earlier (see git log for the full set): pool scoped to the app, vision → OpenRouter,
CGMacros + a release gate that covers it, mobile client rewritten, automatic
free-first model selection, `vision_third_party` consent, dashboard glucose card
fixed, password reset + mailer.

Later in the session, all found by running real traffic rather than reading code:

- [x] `2206f02` **Nothing read `.env`.** `.env.example` documented variables as
      though copying it configured the app; every consumer called `os.environ.get`
      directly. A key in `.env` produced "no API key", which reads as a bad key.
- [x] `3e5c019` **Routers excluded from `auto`.** The first live call through
      `openrouter/free` returned the bare string `'User Safety: safe'` instead of
      JSON — a router's advertised capabilities do not bind the model that answers.
- [x] `15044e3` **Aligned with the published API reference.** Read back the serving
      model from the response (provenance); check errors *inside* choices, not only
      at the top level; `X-OpenRouter-Title`. Corrected an overstated claim from the
      previous commit.
- [x] `f2869dc` **Gateway support** — `PATHYAM_VISION_BASE_URL` for OmniRoute /
      LiteLLM / any OpenAI-compatible proxy, with `auto` refused off OpenRouter.
- [x] `6df1156` **Dev-only enforced** — a non-OpenRouter base URL raises under
      `PATHYAM_ENV=production`.
- [x] *(gateway key fix)* **Stop forwarding an upstream key to a gateway.** Every
      call through OmniRoute returned 401: the gateway holds the upstream credential
      and has no business validating OpenRouter's key.
- [x] `3955573` **Bound `max_tokens`.** Unset is not "no limit", it is the model's
      65,536-token ceiling, and OpenRouter reserves credit against it — a 402 on a
      call that would have emitted a few hundred tokens.

### OmniRoute — evaluated, not adopted
Real project (MIT, ~428 contributors, self-hostable). Ran it locally and routed a
successful vision call through it. **Not adopted**: it ships 12 prompt-compression
engines on by default, and this provider's system prompt carries negations
("do NOT calculate or guess nutrient figures") that are the boundary between
perception and the deterministic engine. Installed in the session scratchpad only;
nothing reached the repo, and `.gitignore` now blocks a stray install.

Operational notes if it is revisited: needs **Node 22 exactly** (`markAsUncloneable`
is Node 22+, and it declares `<24.0.0`); npm blocks its native install scripts by
default so `better-sqlite3` must be rebuilt; providers are configured through the
dashboard, not `.env`; and it auto-discovers local Claude Code / Codex credentials.

---

## 🔄 In Progress (Exact Resume Point)
**Branch:** `main` (and `phase1/restore-trust`, both at the same commit)
**Last commit:** `3955573 fix: bound output tokens — an unset max_tokens is the model's ceiling, not no limit`
**Working tree:** clean, pushed to origin
**Next immediate action:** Nothing half-done. Choose from Remaining Work.

### Eval baseline (2026-09-08, 449 tests passing)
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
vision       248 image-capable, ~3 free+usable; auto:free -> a real model
             (routers now excluded). Free tier rate-limited all day; pin a paid id.
```

---

## 📋 Remaining Work
1. **Run the live vision test once billing is active** (tomorrow). Use a real meal
   photograph — accuracy is still entirely unmeasured.
2. **Set `SMTP_HOST`** before real users, or reset mail goes to the log.
3. **Item 5 (yours):** the CGT tab still shows the illustrative curve, correctly
   labelled. Keep it, hide it, or drop it. The dashboard half is fixed.
4. **Mobile:** photo screen (nothing calls `resolveMealVision`), token persistence,
   and it has still never been installed or built.
5. **Email verification at sign-up** — the other half of item 10.
6. **Item 13:** only `saaru` and `uppittu` are genuinely reachable; `pulihora` is a
   holdout artifact. Fixing 2 of 145 risks overfitting. Recommend leaving it.
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
| Routers excluded from automatic selection | A router's advertised capabilities do not bind the model that answers, and the choice changes per request. Explicit configuration still works | 2026-09-08 |
| `model_version` read back from the response | The serving model is not always the one asked for; recording the request would name a router rather than a reader | 2026-09-08 |
| Gateways are development-only, enforced | They can rewrite prompts in flight and fan out across upstreams, defeating `provider_may_train_on_input` and the consent it backs | 2026-09-08 |
| `max_tokens` always set | Unset is the model's full output ceiling, and providers reserve credit against it | 2026-09-08 |

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
Commit  : 3955573 fix: bound output tokens — an unset max_tokens is the model's ceiling, not no limit
Status  : clean, pushed to origin (main and phase1/restore-trust in sync)
```

Recent commits:
```
3955573 fix: bound output tokens — an unset max_tokens is the model's ceiling, not no limit
ae8c2cd fix: do not forward an upstream provider's key to a gateway
6df1156 feat: refuse a development gateway in production
f2869dc feat: support any OpenAI-compatible gateway, OmniRoute included
15044e3 fix: align the OpenRouter client with the published API reference
3e5c019 fix: never auto-select a router — found by the first real vision call
2206f02 fix: actually read .env — the file .env.example tells you to create
d1a0c97 docs: session handover — OpenRouter, consent, dashboard fix, password reset
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
- **Local AI gateways are dev-only and enforced.** Never set a non-OpenRouter
  `PATHYAM_VISION_BASE_URL` in production; the provider raises. Gateway installs and
  their state are gitignored — their stores hold upstream provider credentials.
- Free OpenRouter endpoints may train on or publish inputs, and the input is a
  photograph of someone's meal.

---

## 🧬 Bioinformatics Context (if applicable)
- Not applicable. Clinical nutrition: FAO/INFOODS tagnames, ICMR-NIN IFCT 2017,
  FAO/INFOODS QC gates. Do not invent local nutrient codes.

---
_Auto-updated by `pre-compact.sh` hook and `/handover` skill._
_Read this at the start of every session. Update with `/handover`._

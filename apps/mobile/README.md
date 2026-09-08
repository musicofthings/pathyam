# Pathyam mobile

Expo React Native client.

## Status — read this before trusting anything here

**This app has never been installed, built or run.** There is no lockfile and no CI
step that compiles it. The TypeScript typechecks; that is the whole of the
verification. Treat a first `npm install` as an unknown.

Expo 51 / React Native 0.74 are roughly two years old as of this writing. Upgrading
is a separate job from the rework below and has not been attempted.

## What changed, and why

The previous `App.tsx` was a demo that fabricated all four of the product's core
claims in the UI, after the same fabrications had been removed from the engine:

- a hardcoded "observed" plate (white rice at confidence 0.93, sambar at 0.88)
  presented as vision output — the exact canned plate the engine's vision provider
  was rewritten to stop returning;
- `setTimeout(1200)` and an alert reading "Meal photo analyzed!", for an analysis
  that never ran;
- **calories computed in the client** as `label.includes('rice') ? 130 : 65` per
  100 g, wrapped in a ±22%/26% interval and shown to a person managing type 2
  diabetes. The engine computes nutrients; the client never does. That rule is what
  the whole system rests on;
- two clinical claims stamped "Verified" by a badge that verified nothing, one
  carrying a real-looking PMID.

None of it called the API. It could not have: it sent no `Authorization` header, and
every endpoint it named has required a bearer token since authentication landed.

It also hardcoded `http://localhost:8000`, which is wrong on any physical device.

## Configuration

There is deliberately **no localhost fallback** — a wrong hardcoded default is
invisible until it reaches a real device. `EXPO_PUBLIC_API_URL` is required and the
app throws at startup without it. This mirrors the backend's `ALLOWED_ORIGINS`,
which likewise refuses to fall back to a wildcard.

```
# apps/mobile/.env
EXPO_PUBLIC_API_URL=http://192.168.1.10:8000
```

On a physical device this must be the host machine's **LAN address**. `localhost` on
a phone is the phone. Add that origin to the backend's `ALLOWED_ORIGINS` too.

## Glucose: HealthKit and Health Connect

`services/glucose.ts` reads blood glucose from Apple Health (iOS) or Health Connect
(Android). Both Dexcom and Abbott FreeStyle Libre write there, so this works with
whatever sensor the user already wears, with no vendor partnership and with the
permission prompt on-device where it can be revoked.

The alternatives are worse for an India-facing product:

| Route | Why not |
|---|---|
| Dexcom API v3 | Real and self-serve, but readings arrive with a **3-hour delay outside the US** (1 hour inside). Fine for retrospective fitting, useless live. |
| Abbott LibreView | No public developer portal; requires a direct partnership, acceptance not guaranteed. Libre is the dominant sensor in India. |
| Aggregators (Validic, Junction, Terra, Vital) | Paid, but the practical way to reach Libre without the Abbott relationship. |

**Native modules — this needs an Expo development build, not Expo Go.**

```bash
npx expo install @kingstinct/react-native-healthkit react-native-health-connect
npx expo run:ios        # or run:android
```

iOS additionally needs `NSHealthShareUsageDescription` in `app.json`; Android needs
the Health Connect permission declaration. Neither is configured yet.

### Two consents, both required

1. **The OS permission**, granted in the platform health app.
2. **Pathyam's `cgm_telemetry` purpose**, granted through `/v1/auth/consent/`.
   Without it `/v1/cgt/telemetry` returns 403, by design — a continuous trace shows
   when someone eats, sleeps, exercises and is ill, so it is granted apart from the
   core service.

Neither implies the other. The OS permission lets the app read; the API grant lets
the server store.

## Still missing

- **The photo path.** `apiClient.resolveMealVision` is wired, but no screen calls it.
  Vision needs `OPENROUTER_API_KEY` and a verified `PATHYAM_VISION_MODEL` server-side
  or it returns 501.
- **Token persistence.** The session lives in memory and is lost on restart.
  `expo-secure-store` is the right home for it.
- **Password reset.** There is none. A forgotten password means an unrecoverable
  account.
- **No tests.** The project has no JavaScript test runner. `services/glucose.ts`
  conversion logic (mmol/L → mg/dL, range rejection) was verified by hand and
  deserves a real test once one exists.

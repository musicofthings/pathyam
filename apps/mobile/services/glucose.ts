/**
 * Glucose bridge — Apple HealthKit (iOS) and Health Connect (Android).
 *
 * WHY THIS ROUTE RATHER THAN A VENDOR API
 * ---------------------------------------
 * Both Dexcom and Abbott FreeStyle Libre write glucose into HealthKit and Health
 * Connect. Reading from the platform store therefore works with whatever sensor the
 * user already wears, needs no vendor partnership, and asks the user for permission
 * on-device where they can see and revoke it.
 *
 * The alternatives are worse for this product:
 *
 *   Dexcom API v3  Real and self-serve, but readings arrive with a **three-hour
 *                  delay outside the United States** (one hour inside). India is
 *                  outside. Fine for retrospective fitting, useless for anything a
 *                  user watches.
 *   Abbott         No public developer portal. LibreView access requires a direct
 *                  partnership and acceptance is not guaranteed — and Libre is the
 *                  dominant sensor in India. Community-reverse-engineered endpoints
 *                  exist; they are not something to put in a shipped product.
 *
 * NATIVE MODULES: this needs an Expo **development build**, not Expo Go. Both
 * libraries below ship native code. See apps/mobile/README.md.
 *
 * CONSENT: two separate things, and both are required.
 *   1. The OS permission, granted here.
 *   2. Pathyam's own `cgm_telemetry` purpose, granted through the API. Without it
 *      /v1/cgt/telemetry returns 403 — a continuous trace shows when someone eats,
 *      sleeps, exercises and is ill, so it is granted apart from the core service.
 * Neither implies the other. The OS permission lets us read; the API grant lets us
 * store.
 */

import { Platform } from 'react-native';

import { CONSENT_CGM_TELEMETRY } from './config';
import { ApiError, GlucoseReading, PathyamAPIClient } from './apiClient';

/** HealthKit reports mg/dL or mmol/L depending on the writing app and locale. */
const MMOL_TO_MG_DL = 18.0182;

/** The server's CHECK constraint. Outside this is a sensor error, not a reading. */
const MIN_MG_DL = 40;
const MAX_MG_DL = 450;

export interface PlatformSample {
  /** Milliseconds since epoch, or an ISO string. */
  startDate: string | number;
  value: number;
  unit?: string;
}

/**
 * Reads raw samples from the platform health store. Injectable so the conversion
 * and upload logic below can be tested without a device.
 */
export interface HealthStore {
  requestPermission(): Promise<boolean>;
  readGlucose(since: Date, until: Date): Promise<PlatformSample[]>;
}

/**
 * Normalise one platform sample.
 *
 * Returns null rather than a clamped or guessed value. A reading outside the
 * sensor's range is an error, and clamping it to 40 or 450 would invent a
 * measurement at the boundary that the fitting code could not tell from a real one.
 */
export function toReading(sample: PlatformSample): GlucoseReading | null {
  const unit = (sample.unit ?? '').toLowerCase();
  // HealthKit's canonical glucose unit is mg/dL, but apps may write mmol/L. A
  // mmol/L value read as mg/dL lands around 5–10 — below the CHECK, so it would be
  // rejected server-side rather than silently stored, but converting is correct.
  const mgdl = unit.includes('mmol') ? sample.value * MMOL_TO_MG_DL : sample.value;

  if (!Number.isFinite(mgdl) || mgdl < MIN_MG_DL || mgdl > MAX_MG_DL) return null;

  const when = typeof sample.startDate === 'number' ? new Date(sample.startDate) : new Date(sample.startDate);
  if (Number.isNaN(when.getTime())) return null;

  return {
    timestamp: when.toISOString(),
    glucose_mg_dl: Math.round(mgdl * 10) / 10, // numeric(5,1) server-side
    // Deliberately not derived here. A trend arrow computed from two samples is a
    // different quantity from the one a CGM reports, and the server stores whatever
    // it is told. 'flat' is the schema default and is the honest "not reported".
    trend_arrow: 'flat',
  };
}

export interface SyncResult {
  read: number;
  uploaded: number;
  accepted: number;
  duplicates: number;
  rejected: number;
}

export class GlucoseSync {
  constructor(
    private readonly store: HealthStore,
    private readonly api: PathyamAPIClient,
  ) {}

  /**
   * Read a window of glucose from the platform store and upload it.
   *
   * Idempotent end to end: the server upserts on (user, reading time), so
   * overlapping windows and retries are safe. Overlap is deliberate on the caller's
   * side — a sensor that reconnects backfills readings with earlier timestamps than
   * the last one seen, so syncing strictly forward from the newest timestamp loses
   * exactly the readings a connectivity gap produced.
   */
  async sync(since: Date, until: Date = new Date()): Promise<SyncResult> {
    const granted = await this.store.requestPermission();
    if (!granted) {
      throw new Error('permission to read glucose was not granted');
    }

    const samples = await this.store.readGlucose(since, until);
    const readings: GlucoseReading[] = [];
    for (const sample of samples) {
      const reading = toReading(sample);
      if (reading) readings.push(reading);
    }
    const rejected = samples.length - readings.length;

    if (readings.length === 0) {
      return { read: samples.length, uploaded: 0, accepted: 0, duplicates: 0, rejected };
    }

    try {
      const result = await this.api.uploadGlucose(readings);
      return {
        read: samples.length,
        uploaded: readings.length,
        accepted: result.accepted,
        duplicates: result.duplicates,
        rejected,
      };
    } catch (error) {
      // A missing consent grant is actionable — ask the user — and must not look
      // like a network failure the app should silently retry.
      if (error instanceof ApiError && error.missingConsentPurpose === CONSENT_CGM_TELEMETRY) {
        throw new Error(
          'Glucose upload needs your consent. Grant the "cgm_telemetry" purpose, ' +
            'then sync again.',
        );
      }
      throw error;
    }
  }
}

/**
 * The platform store for the device this is running on.
 *
 * Both modules are native and require a development build. They are imported
 * lazily so that a build without them fails here, with a message naming the
 * missing package, rather than at app startup.
 */
export async function platformHealthStore(): Promise<HealthStore> {
  if (Platform.OS === 'ios') return iosHealthKitStore();
  if (Platform.OS === 'android') return androidHealthConnectStore();
  throw new Error(`no glucose source on platform ${Platform.OS}`);
}

async function iosHealthKitStore(): Promise<HealthStore> {
  const HealthKit = await importOrExplain(
    () => import('@kingstinct/react-native-healthkit'),
    '@kingstinct/react-native-healthkit',
  );
  const kit = (HealthKit as { default?: unknown }).default ?? HealthKit;
  const hk = kit as {
    requestAuthorization(read: string[]): Promise<boolean>;
    queryQuantitySamples(
      type: string,
      opts: { from: Date; to: Date },
    ): Promise<Array<{ startDate: string | number; quantity: number; unit?: string }>>;
  };

  const TYPE = 'HKQuantityTypeIdentifierBloodGlucose';
  return {
    // Read-only. This app never writes to the health store: a value Pathyam wrote
    // could be read back by another app as though a sensor had measured it.
    requestPermission: () => hk.requestAuthorization([TYPE]),
    readGlucose: async (since, until) => {
      const samples = await hk.queryQuantitySamples(TYPE, { from: since, to: until });
      return samples.map((s) => ({ startDate: s.startDate, value: s.quantity, unit: s.unit }));
    },
  };
}

async function androidHealthConnectStore(): Promise<HealthStore> {
  const HC = await importOrExplain(
    () => import('react-native-health-connect'),
    'react-native-health-connect',
  );
  const hc = HC as {
    initialize(): Promise<boolean>;
    requestPermission(
      perms: Array<{ accessType: string; recordType: string }>,
    ): Promise<unknown[]>;
    readRecords(
      recordType: string,
      opts: { timeRangeFilter: { operator: string; startTime: string; endTime: string } },
    ): Promise<{ records: Array<{ time: string; level: { inMilligramsPerDeciliter: number } }> }>;
  };

  await hc.initialize();
  return {
    requestPermission: async () => {
      const granted = await hc.requestPermission([
        { accessType: 'read', recordType: 'BloodGlucose' },
      ]);
      return granted.length > 0;
    },
    readGlucose: async (since, until) => {
      const { records } = await hc.readRecords('BloodGlucose', {
        timeRangeFilter: {
          operator: 'between',
          startTime: since.toISOString(),
          endTime: until.toISOString(),
        },
      });
      // Health Connect exposes the value already converted, so the unit is known.
      return records.map((r) => ({
        startDate: r.time,
        value: r.level.inMilligramsPerDeciliter,
        unit: 'mg/dL',
      }));
    },
  };
}

async function importOrExplain<T>(load: () => Promise<T>, packageName: string): Promise<T> {
  try {
    return await load();
  } catch (error) {
    throw new Error(
      `${packageName} is not installed or this is not a development build. ` +
        'Glucose reading uses native modules and does not work in Expo Go — ' +
        'see apps/mobile/README.md.',
    );
  }
}

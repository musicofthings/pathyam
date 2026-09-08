/**
 * Pathyam API client.
 *
 * Every endpoint below exists in engine/pathyam_api/main.py. The previous version of
 * this file documented endpoints by their intended behaviour rather than the server's
 * ("Gemini 3.7 Flash", a model that does not exist), hardcoded `http://localhost:8000`,
 * and sent no Authorization header at all — so every call it described would have
 * returned 401 against the current API, which has required a bearer token since
 * authentication landed.
 */

import { apiBaseUrl } from './config';

export interface AuthSession {
  access_token: string;
  token_type: string;
  user_id: string;
  expires_at: string;
}

export interface GlucoseReading {
  timestamp: string; // ISO 8601
  glucose_mg_dl: number; // 40–450, enforced server-side
  trend_arrow?: 'rapidly_rising' | 'rising' | 'flat' | 'falling' | 'rapidly_falling';
}

export interface TelemetryResult {
  status: 'ok' | 'error';
  accepted: number;
  duplicates: number;
  total_stored: number;
}

/** An error carrying the server's status, so callers can branch on it. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** 401: the session is gone or expired. The caller should re-authenticate. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /**
   * 403 with a `purpose` in the detail: the write needs a consent grant the user
   * has not given. Distinct from a plain permission failure — it is actionable,
   * and the action is to ask the user, not to retry.
   */
  get missingConsentPurpose(): string | null {
    const detail = this.detail as { purpose?: unknown } | null;
    if (this.status === 403 && detail && typeof detail.purpose === 'string') {
      return detail.purpose;
    }
    return null;
  }
}

export class PathyamAPIClient {
  private token: string | null = null;

  constructor(private readonly baseUrl: string = apiBaseUrl()) {}

  setToken(token: string | null): void {
    this.token = token;
  }

  get hasToken(): boolean {
    return this.token !== null;
  }

  private async request<T>(
    path: string,
    init: RequestInit & { auth?: boolean } = {},
  ): Promise<T> {
    const { auth = true, headers, ...rest } = init;

    if (auth && !this.token) {
      throw new ApiError(401, null, 'not signed in');
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...rest,
      headers: {
        Accept: 'application/json',
        ...(auth && this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        ...headers,
      },
    });

    if (!response.ok) {
      // FastAPI puts the useful part in `detail`, which may be a string or an
      // object. Surfacing the raw status alone loses the consent purpose and the
      // "no vision model configured" text, which are the two errors a user can act on.
      let detail: unknown = null;
      try {
        detail = (await response.json())?.detail ?? null;
      } catch {
        detail = null;
      }
      const summary =
        typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : response.statusText;
      throw new ApiError(response.status, detail, `${path} failed (${response.status}): ${summary}`);
    }

    return (await response.json()) as T;
  }

  private json<T>(path: string, body: unknown, auth = true): Promise<T> {
    return this.request<T>(path, {
      auth,
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  // ------------------------------------------------------------------ auth --

  async register(email: string, password: string): Promise<AuthSession> {
    const session = await this.json<AuthSession>('/v1/auth/register', { email, password }, false);
    this.setToken(session.access_token);
    return session;
  }

  async login(email: string, password: string): Promise<AuthSession> {
    const session = await this.json<AuthSession>('/v1/auth/login', { email, password }, false);
    this.setToken(session.access_token);
    return session;
  }

  async logout(everywhere = false): Promise<void> {
    await this.json(`/v1/auth/logout${everywhere ? '?everywhere=true' : ''}`, {});
    this.setToken(null);
  }

  whoami(): Promise<{ user_id: string }> {
    return this.request('/v1/auth/me');
  }

  /**
   * Grant an optional processing purpose. `cgm_telemetry` gates every glucose
   * write: without it /v1/cgt/telemetry returns 403, by design.
   */
  grantConsent(purposeKey: string): Promise<{ status: string; purpose: string; granted: boolean }> {
    return this.json(`/v1/auth/consent/${encodeURIComponent(purposeKey)}`, {});
  }

  // ----------------------------------------------------------------- meals --

  /** Resolve free text and compute in one call — the primary logging path. */
  logMeal(text: string, opts: { regionKey?: string; nSamples?: number } = {}): Promise<unknown> {
    return this.json('/v1/log', {
      text,
      region_key: opts.regionKey,
      n_samples: opts.nSamples ?? 500,
    });
  }

  history(): Promise<{ entries: Array<{ id: string; [k: string]: unknown }> }> {
    return this.request('/v1/history');
  }

  dashboardSummary(): Promise<unknown> {
    return this.request('/v1/dashboard/summary');
  }

  // ---------------------------------------------------------------- vision --

  /**
   * Upload a meal photo for perception and entity resolution.
   *
   * Requires the `vision_third_party` consent purpose: the photo leaves Pathyam
   * and is processed by an external provider. Without it the server returns 403
   * naming the purpose, which ApiError.missingConsentPurpose surfaces.
   *
   * Returns 501 when no vision model is configured server-side, and 502 when the
   * upstream call fails. Neither is retryable by the client and neither returns a
   * fabricated meal — the provider used to fall back to a canned plate of dosa and
   * sambar that callers could not distinguish from a real reading.
   */
  async resolveMealVision(imageUri: string, regionKey?: string): Promise<unknown> {
    const form = new FormData();
    form.append('file', {
      uri: imageUri,
      name: imageUri.split('/').pop() || 'meal.jpg',
      type: 'image/jpeg',
    } as unknown as Blob);
    if (regionKey) form.append('region_key', regionKey);

    // Content-Type is deliberately unset: fetch must add its own multipart boundary.
    return this.request('/v1/vision/resolve', { method: 'POST', body: form });
  }

  // -------------------------------------------------------------- telemetry --

  /**
   * Upload glucose readings. Idempotent on (user, reading time) server-side, so a
   * replayed batch updates rather than duplicating — a doubled reading would bias
   * anything later fitted from the table.
   */
  uploadGlucose(readings: GlucoseReading[]): Promise<TelemetryResult> {
    if (readings.length === 0) {
      // The server requires min_length=1; sending an empty batch is a client bug.
      return Promise.resolve({ status: 'ok', accepted: 0, duplicates: 0, total_stored: 0 });
    }
    return this.json('/v1/cgt/telemetry', { readings });
  }

  /** Measured glucose in the 3h after one meal, beside the (illustrative) curve. */
  postprandial(mealLogId: string): Promise<unknown> {
    return this.request(`/v1/cgt/postprandial/${encodeURIComponent(mealLogId)}`);
  }
}

export const apiClient = new PathyamAPIClient();

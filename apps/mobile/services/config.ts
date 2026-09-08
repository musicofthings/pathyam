/**
 * Runtime configuration.
 *
 * There is deliberately no `http://localhost:8000` fallback. A hardcoded localhost
 * default is invisible when it is wrong: the app builds, ships, and fails only on a
 * real device that has no server on its own loopback interface. Unset now throws at
 * startup, where it is obvious.
 *
 * This mirrors the backend's ALLOWED_ORIGINS, which also refuses to fall back to a
 * wildcard — see engine/pathyam_api/main.py and .env.example.
 *
 * Set EXPO_PUBLIC_API_URL in `.env` (Expo inlines EXPO_PUBLIC_* at build time):
 *
 *     EXPO_PUBLIC_API_URL=http://192.168.1.10:8000     # LAN IP, not localhost:
 *                                                      # a device is not the host
 */

const RAW = process.env.EXPO_PUBLIC_API_URL;

export function apiBaseUrl(): string {
  const configured = (RAW ?? '').trim();
  if (!configured) {
    throw new Error(
      'EXPO_PUBLIC_API_URL is not set. Point it at the Pathyam API — on a physical ' +
        'device this must be the host machine\'s LAN address, not localhost.',
    );
  }
  return configured.replace(/\/+$/, '');
}

export const CONSENT_CGM_TELEMETRY = 'cgm_telemetry';
/** Sending a meal photograph out of Pathyam to an external model provider. */
export const CONSENT_VISION_THIRD_PARTY = 'vision_third_party';

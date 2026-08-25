/**
 * Pathyam Mobile Client API Service.
 *
 * Connects Expo React Native mobile app to FastAPI backend endpoints:
 *   - POST /v1/vision/resolve  (Meal Photo Upload -> Quality Gate -> Gemini 3.7 Flash -> Candidates)
 *   - POST /v1/compute         (Parametric Deterministic Compute -> Monte Carlo Intervals)
 *   - POST /v1/log             (Resolve + Compute in one call)
 *   - GET  /v1/history         (Logged Meals & Dashboard Summary)
 *   - POST /v1/evidence/explain (Evidence Engine Clinical Explanation)
 */

import {
  VisionResolveResponse,
  ClinicalExplanationResponse,
} from '../../packages/api-schema';

const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';

export class PathyamAPIClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  /**
   * Upload meal photo for Gemini 3.7 Flash perception & entity resolution.
   */
  async resolveMealVision(imageUri: string, regionKey?: string): Promise<VisionResolveResponse> {
    const formData = new FormData();
    const filename = imageUri.split('/').pop() || 'meal_photo.jpg';
    
    // Append image file for multipart upload
    formData.append('file', {
      uri: imageUri,
      name: filename,
      type: 'image/jpeg',
    } as any);

    if (regionKey) {
      formData.append('region_key', regionKey);
    }

    const response = await fetch(`${this.baseUrl}/v1/vision/resolve`, {
      method: 'POST',
      body: formData,
      headers: {
        'Accept': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`Vision API resolve failed with status ${response.status}`);
    }

    return response.json();
  }

  /**
   * Execute deterministic calculation over template & parameter overrides.
   */
  async computeNutrition(template: string, paramOverrides: Record<string, number>, nSamples: number = 500) {
    const response = await fetch(`${this.baseUrl}/v1/compute`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        template,
        param_overrides: paramOverrides,
        n_samples: nSamples,
      }),
    });

    if (!response.ok) {
      throw new Error(`Compute API failed with status ${response.status}`);
    }

    return response.json();
  }

  /**
   * Query Evidence Engine for clinical explanations with verified PMID/DOI citations.
   */
  async getClinicalExplanation(query: string): Promise<ClinicalExplanationResponse> {
    const response = await fetch(`${this.baseUrl}/v1/evidence/explain?query=${encodeURIComponent(query)}`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`Evidence API failed with status ${response.status}`);
    }

    return response.json();
  }
}

export const apiClient = new PathyamAPIClient();

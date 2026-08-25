/**
 * Shared API Schema & TypeScript Data Contracts for Pathyam.
 *
 * Enforces strict separation: LLMs perceive & explain; deterministic engine resolves & computes.
 */

export interface Interval {
  p10: number;
  p50: number;
  p90: number;
  mean: number;
  sd: number;
}

export interface PortionEstimate {
  grams: number | null;
  millilitres: number | null;
  uncertainty: 'low' | 'medium' | 'high';
  min_grams: number | null;
  max_grams: number | null;
}

export interface CandidateFood {
  food_id: number;
  pathyam_id: string;
  name_en: string;
  matched_text: string;
  lang: string;
  score: number;
  base_similarity: number;
  method: 'exact' | 'trigram' | 'phonetic' | 'vector';
  template_id: number | null;
  food_group: string | null;
}

export interface VisionObservationItem {
  visual_label: string;
  estimated_portion: PortionEstimate;
  preparation: string;
  count: number | null;
  modifiers: string[];
  confidence: number;
  candidates: CandidateFood[];
}

export interface VisionQualityGate {
  passed: boolean;
  quality_score: number;
  issues: string[];
}

export interface VisionResolveResponse {
  quality_gate: VisionQualityGate;
  observations: VisionObservationItem[];
  model_version: string;
}

export interface EvidenceCitation {
  is_valid: boolean;
  pmid?: string;
  doi?: string;
  verification_source: string;
  title_match?: string;
  suppressed: boolean;
}

export interface EvidenceClaim {
  statement: string;
  evidence_ids: string[];
  certainty: 'high' | 'moderate' | 'low';
  verified_citations: EvidenceCitation[];
}

export interface ClinicalExplanationResponse {
  clinical_explanation: string;
  claims: EvidenceClaim[];
  suppressed_citations_count: number;
}

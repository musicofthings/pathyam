/**
 * Shared Food Types & Nutrient Metadata for Pathyam.
 */

export interface Nutrient {
  nutrient_id: number;
  infoods_tag: string;
  canonical_name: string;
  unit: string;
  decimals: number;
  group: 'proximate' | 'lipid' | 'carbohydrate' | 'mineral' | 'vitamin' | 'other';
  is_core: boolean;
}

export interface FoodItem {
  food_id: number;
  pathyam_id: string;
  canonical_name: string;
  food_group: string;
  ifct_code?: string;
  density_g_per_ml?: number;
  edible_portion_pct?: number;
}

export interface RecipeState {
  DRAFT: 'DRAFT';
  RESOLUTION_REQUIRED: 'RESOLUTION_REQUIRED';
  MISSING_COMPOSITION: 'MISSING_COMPOSITION';
  MISSING_QUANTITY: 'MISSING_QUANTITY';
  QC_FAILED: 'QC_FAILED';
  COMPUTABLE: 'COMPUTABLE';
  VALIDATED: 'VALIDATED';
}

export const CORE_NUTRIENT_TAGS = [
  'ENERC_KCAL',
  'PROCNT',
  'FAT',
  'CHOAVLDF',
  'FIBTG',
  'NA',
  'K',
] as const;

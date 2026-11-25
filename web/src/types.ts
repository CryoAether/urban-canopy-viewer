export interface AnalyzeResponse {
  location_name: string;
  bbox: number[];
  baseline_year: number;
  target_year: number;
  greenery_coverage_pct: number;
  canopy_change_pct: number;
  total_area_analyzed_km2: number;
  images: Record<string, string>;
}
import { AnalyzeResponse } from "./types";

const API_BASE = import.meta.env.VITE_API_URL || "";

export async function analyzeTown(
  town: string,
  baselineYear: number = 2020,
  targetYear: number = 2024
): Promise<AnalyzeResponse> {
  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      town,
      baseline_year: baselineYear,
      target_year: targetYear,
    }),
  });

  if (!res.ok) {
    let message = "Unable to analyze this location. Please try another city or town.";
    try {
      const errData = await res.json();
      if (errData?.detail) {
        message = errData.detail;
      }
    } catch {
      // Fallback if response is not JSON
    }
    throw new Error(message);
  }

  return await res.json();
}
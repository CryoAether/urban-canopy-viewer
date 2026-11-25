import React, { useState, useRef, useEffect } from "react";
import { analyzeTown } from "./api";
import { AnalyzeResponse } from "./types";

const AVAILABLE_YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026];

export default function App() {
  const [query, setQuery] = useState("");
  const [baselineYear, setBaselineYear] = useState<number>(2020);
  const [targetYear, setTargetYear] = useState<number>(2024);
  const [data, setData] = useState<AnalyzeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sliderPos, setSliderPos] = useState(50);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const isDragging = useRef(false);

  const handleAnalyze = async (
    searchTown: string,
    bYear: number = baselineYear,
    tYear: number = targetYear
  ) => {
    if (!searchTown.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await analyzeTown(searchTown, bYear, tYear);
      setData(result);
      setSliderPos(50);
    } catch (e: any) {
      setError(e.message || "Failed to analyze region");
    } finally {
      setLoading(false);
    }
  };

  const updateSlider = (clientX: number) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const pct = ((clientX - rect.left) / rect.width) * 100;
    setSliderPos(Math.max(2, Math.min(98, pct)));
  };

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (isDragging.current) updateSlider(e.clientX);
    };
    const onTouchMove = (e: TouchEvent) => {
      if (isDragging.current && e.touches[0]) updateSlider(e.touches[0].clientX);
    };
    const stopDrag = () => {
      isDragging.current = false;
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("touchmove", onTouchMove);
    window.addEventListener("mouseup", stopDrag);
    window.addEventListener("touchend", stopDrag);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("mouseup", stopDrag);
      window.removeEventListener("touchend", stopDrag);
    };
  }, []);

  const formatTitle = (fullName: string) => {
    const parts = fullName.split(",").map((p) => p.trim());
    return parts.slice(0, 2).join(", ");
  };

  const activeBaseline = data?.baseline_year ?? baselineYear;
  const activeTarget = data?.target_year ?? targetYear;

  return (
    <div className="app-shell">
      <main className="main-content">
        {!data && !loading && (
          <div className="idle-beacon-wrap" aria-hidden="true">
            <span className="idle-beacon-ring" />
            <span className="idle-beacon-dot" />
          </div>
        )}

        {data && (
          <div className="card-with-legend">
            <div className={`result-card ${loading ? "is-loading" : ""}`}>
              <div className="card-header">
                <div>
                  <h2 className="location-title">{formatTitle(data.location_name)}</h2>
                </div>
                <div className="metric-block">
                  <div className="metric-row">
                    <span className="big-metric">{data.greenery_coverage_pct.toFixed(1)}</span>
                    <span className="metric-unit">%</span>
                  </div>
                  <span className="metric-caption">vegetated land cover ({activeTarget})</span>
                </div>
              </div>

              <div
                className="slider-container"
                ref={containerRef}
                onMouseDown={(e) => {
                  isDragging.current = true;
                  updateSlider(e.clientX);
                }}
                onTouchStart={(e) => {
                  isDragging.current = true;
                  if (e.touches[0]) updateSlider(e.touches[0].clientX);
                }}
              >
                {/* Base Layer (Right Side): Target Year */}
                <img
                  src={data.images[String(activeTarget)]}
                  alt={`${activeTarget} Satellite Canopy`}
                  className="satellite-img"
                  draggable={false}
                />

                {/* Top Clipped Layer (Left Side): Baseline Year */}
                <img
                  src={data.images[String(activeBaseline)]}
                  alt={`${activeBaseline} Satellite Canopy`}
                  className="satellite-img"
                  style={{ clipPath: `inset(0 ${100 - sliderPos}% 0 0)` }}
                  draggable={false}
                />

                {/* Interactive Divider */}
                <div className="slider-line" style={{ left: `${sliderPos}%` }}>
                  <div className="slider-handle">
                    <span className="handle-arrows">◀ ▶</span>
                  </div>
                </div>

                {/* Corner Labels */}
                <div className={`year-pill year-left ${sliderPos < 14 ? "is-hidden" : ""}`}>
                  ← {activeBaseline}
                </div>
                <div className={`year-pill year-right ${sliderPos > 86 ? "is-hidden" : ""}`}>
                  {activeTarget} →
                </div>
              </div>

              <div className="card-footer">
                <span className="area-meta">{data.total_area_analyzed_km2} km² surveyed</span>
                <span className={`delta-badge ${data.canopy_change_pct >= 0 ? "pos" : "neg"}`}>
                  {data.canopy_change_pct >= 0 ? "+" : ""}
                  {data.canopy_change_pct.toFixed(1)}% shift vs {activeBaseline}
                </span>
              </div>
            </div>

            {/* External Right-Side Map Legend */}
            <aside className="map-legend" aria-label="Map Legend">
              <div className="legend-heading">Surface Key</div>
              <div className="legend-item">
                <span className="swatch swatch-dense" />
                <div>
                  <div className="legend-label">Dense Canopy</div>
                  <div className="legend-sub">Trees &amp; thick foliage</div>
                </div>
              </div>
              <div className="legend-item">
                <span className="swatch swatch-moderate" />
                <div>
                  <div className="legend-label">Parks &amp; Grass</div>
                  <div className="legend-sub">Moderate greenery</div>
                </div>
              </div>
              <div className="legend-item">
                <span className="swatch swatch-urban" />
                <div>
                  <div className="legend-label">Urban / Bare</div>
                  <div className="legend-sub">Buildings, roads, soil</div>
                </div>
              </div>
              <div className="legend-item">
                <span className="swatch swatch-water" />
                <div>
                  <div className="legend-label">Water</div>
                  <div className="legend-sub">Ocean, lakes, rivers</div>
                </div>
              </div>
              <div className="legend-item">
                <span className="swatch swatch-boundary" />
                <div>
                  <div className="legend-label">Boundary</div>
                  <div className="legend-sub">Surveyed region</div>
                </div>
              </div>
            </aside>
          </div>
        )}

        {!data && loading && (
          <div className="loading-card">
            <div className="spinner" />
            <p>Analyzing Sentinel-2 imagery for {query}...</p>
          </div>
        )}

        <div className="search-section">
          <h1 className="hero-title">How green is your town?</h1>
          <form
            className="search-form"
            onSubmit={(e) => {
              e.preventDefault();
              handleAnalyze(query, baselineYear, targetYear);
            }}
          >
            <input
              type="text"
              className="search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Enter any town or city..."
              disabled={loading}
            />

            <div className="year-selectors">
              <select
                className="year-select"
                value={baselineYear}
                onChange={(e) => setBaselineYear(Number(e.target.value))}
                disabled={loading}
                aria-label="Baseline year"
              >
                {AVAILABLE_YEARS.map((y) => (
                  <option key={y} value={y} disabled={y >= targetYear}>
                    {y}
                  </option>
                ))}
              </select>
              <span className="year-sep">vs</span>
              <select
                className="year-select"
                value={targetYear}
                onChange={(e) => setTargetYear(Number(e.target.value))}
                disabled={loading}
                aria-label="Target year"
              >
                {AVAILABLE_YEARS.map((y) => (
                  <option key={y} value={y} disabled={y <= baselineYear}>
                    {y}
                  </option>
                ))}
              </select>
            </div>

            <button type="submit" className="search-btn" disabled={loading}>
              {loading ? "Analyzing..." : "Analyze"}
            </button>
          </form>
          {error && <div className="error-msg">{error}</div>}
        </div>
      </main>
    </div>
  );
}
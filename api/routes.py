from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
import rasterio.features
from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import from_shape
from pydantic import BaseModel
from shapely.geometry import box, shape
from skimage.transform import resize
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db
from database.models import TownAnalysisCache
from src.constants import RASTER_HEIGHT, RASTER_SHAPE, RASTER_WIDTH
from src.fast_engine import fast_array_to_png, get_fast_year_composite

router = APIRouter(prefix="/api")

DEMO_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


class AnalyzeReq(BaseModel):
    town: str
    baseline_year: int = 2020
    target_year: int = 2024


def load_demo_image_b64(filename: str) -> Optional[str]:
    path = DEMO_ASSETS_DIR / filename
    if path.exists():
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/png;base64,{encoded}"
    return None


async def geocode_location(
    query: str,
) -> Tuple[str, float, float, Optional[Dict[str, Any]], Optional[List[float]]]:
    headers = {"User-Agent": "UrbanCanopyViewer/2.0 (research; educational)"}
    timeout = httpx.Timeout(10.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "polygon_geojson": 1, "limit": 1},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    item = data[0]
                    bb = item.get("boundingbox")
                    raw_bbox = None
                    if bb and len(bb) == 4:
                        raw_bbox = [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])]
                    return (
                        item.get("display_name", query),
                        float(item["lat"]),
                        float(item["lon"]),
                        item.get("geojson"),
                        raw_bbox,
                    )
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            resp = await client.get(
                "https://photon.komoot.io/api/",
                params={"q": query, "limit": 1},
            )
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                if features:
                    feat = features[0]
                    lon, lat = feat["geometry"]["coordinates"]
                    props = feat.get("properties", {})
                    name = props.get("name", query)
                    state = props.get("state", "")
                    country = props.get("country", "")
                    display = ", ".join(filter(None, [name, state, country]))

                    raw_bbox = feat.get("properties", {}).get("extent")
                    if raw_bbox and len(raw_bbox) == 4:
                        raw_bbox = [raw_bbox[0], raw_bbox[3], raw_bbox[2], raw_bbox[1]]
                    return display, float(lat), float(lon), feat.get("geometry"), raw_bbox
    except Exception:
        raise HTTPException(
            status_code=504,
            detail="Geocoding service timed out. Please try again.",
        )

    raise HTTPException(
        status_code=404,
        detail=f"Could not locate '{query}'. Try including a state or country.",
    )


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/status")
async def status():
    return {"status": "ready", "ok": True}


@router.post("/analyze")
async def analyze_town(req: AnalyzeReq, db: AsyncSession = Depends(get_db)):
    query_clean = req.town.strip().lower()

    # 1. Check PostGIS spatial/query cache first
    try:
        cached_stmt = select(TownAnalysisCache).where(
            TownAnalysisCache.query_key == query_clean,
            TownAnalysisCache.baseline_year == req.baseline_year,
            TownAnalysisCache.target_year == req.target_year,
        )
        cached_res = await db.execute(cached_stmt)
        cached = cached_res.scalar_one_or_none()
        if cached:
            return {
                "location_name": cached.location_name,
                "bbox": [],
                "greenery_coverage_pct": cached.greenery_coverage_pct,
                "canopy_change_pct": cached.canopy_change_pct,
                "total_area_analyzed_km2": cached.total_area_analyzed_km2,
                "images": {
                    str(req.baseline_year): cached.image_baseline_b64,
                    str(req.target_year): cached.image_target_b64,
                },
            }
    except Exception:
        pass

    # 2. Geocode location
    display_name, lat, lon, geom_geojson, raw_bbox = await geocode_location(req.town)

    has_polygon = geom_geojson and geom_geojson.get("type") in ["Polygon", "MultiPolygon"]
    TARGET_ASPECT = 2.0
    poly_geom = None

    if has_polygon:
        try:
            poly_geom = shape(geom_geojson)
            p_minx, p_miny, p_maxx, p_maxy = poly_geom.bounds

            raw_w = p_maxx - p_minx
            raw_h = p_maxy - p_miny
            c_lon = (p_minx + p_maxx) / 2.0
            c_lat = (p_miny + p_maxy) / 2.0
            cos_lat = np.cos(np.radians(c_lat))

            span_lat = max(0.04, raw_h * 1.30)
            span_lon = max(0.08, raw_w * 1.30)

            if (span_lon / (span_lat * cos_lat)) < TARGET_ASPECT:
                span_lon = span_lat * cos_lat * TARGET_ASPECT
            else:
                span_lat = span_lon / (TARGET_ASPECT * cos_lat)

            bbox = [
                c_lon - span_lon / 2.0,
                c_lat - span_lat / 2.0,
                c_lon + span_lon / 2.0,
                c_lat + span_lat / 2.0,
            ]
        except Exception:
            has_polygon = False

    if not has_polygon and raw_bbox:
        raw_w = raw_bbox[2] - raw_bbox[0]
        raw_h = raw_bbox[3] - raw_bbox[1]
        c_lon = (raw_bbox[0] + raw_bbox[2]) / 2.0
        c_lat = (raw_bbox[1] + raw_bbox[3]) / 2.0
        cos_lat = np.cos(np.radians(c_lat))

        span_lat = max(0.04, raw_h * 1.30)
        span_lon = max(0.08, raw_w * 1.30)

        if (span_lon / (span_lat * cos_lat)) < TARGET_ASPECT:
            span_lon = span_lat * cos_lat * TARGET_ASPECT
        else:
            span_lat = span_lon / (TARGET_ASPECT * cos_lat)

        bbox = [c_lon - span_lon / 2.0, c_lat - span_lat / 2.0, c_lon + span_lon / 2.0, c_lat + span_lat / 2.0]

    elif not has_polygon:
        delta_lat = 4.0 / 111.0
        delta_lon = (delta_lat * TARGET_ASPECT) / np.cos(np.radians(lat))
        bbox = [lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat]

    # Guard against country/continent-scale queries & clamp to valid WGS84 bounds
    if (bbox[2] - bbox[0]) > 5.0 or (bbox[3] - bbox[1]) > 5.0:
        raise HTTPException(
            status_code=400,
            detail="Region is too large. Please search for a specific city, town, or neighborhood.",
        )

    bbox = [
        max(-180.0, bbox[0]),
        max(-90.0, bbox[1]),
        min(180.0, bbox[2]),
        min(90.0, bbox[3]),
    ]

    lat_dist = abs(bbox[3] - bbox[1]) * 111.0
    lon_dist = abs(bbox[2] - bbox[0]) * 111.0 * np.cos(np.radians(lat))
    area_km2 = max(1.0, lat_dist * lon_dist)

    # 3. Stream Sentinel-2 COG windows in parallel
    target_poly = geom_geojson if has_polygon else None
    ndvi_2020_task = get_fast_year_composite(bbox, req.baseline_year, shape=RASTER_SHAPE, geom_geojson=target_poly)
    ndvi_2024_task = get_fast_year_composite(bbox, req.target_year, shape=RASTER_SHAPE, geom_geojson=target_poly)
    ndvi_baseline, ndvi_target = await asyncio.gather(ndvi_2020_task, ndvi_2024_task)

    if ndvi_baseline.shape != ndvi_target.shape:
        ndvi_baseline = resize(ndvi_baseline, ndvi_target.shape, order=1, preserve_range=True)

    valid_land = np.isfinite(ndvi_target) & (ndvi_target > 0.08)

    if has_polygon:
        try:
            inv_transform = rasterio.transform.from_bounds(
                bbox[0], bbox[1], bbox[2], bbox[3], RASTER_WIDTH, RASTER_HEIGHT
            )
            inside_city = rasterio.features.rasterize(
                [(geom_geojson, 1)],
                out_shape=RASTER_SHAPE,
                transform=inv_transform,
                fill=0,
                dtype=np.uint8,
            ).astype(bool)

            if np.any(inside_city):
                valid_land = valid_land & inside_city
        except Exception:
            pass

    # Compare identical cloud-free land pixels across both years
    valid_both = valid_land & np.isfinite(ndvi_baseline) & (ndvi_baseline > 0.08)
    total_land_pixels = int(np.count_nonzero(valid_both))

    foliage_target = (ndvi_target >= 0.36) & valid_both
    foliage_baseline = (ndvi_baseline >= 0.36) & valid_both

    green_now = int(np.count_nonzero(foliage_target))
    green_prev = int(np.count_nonzero(foliage_baseline))

    greenery_pct = round(float((green_now / max(1, total_land_pixels)) * 100.0), 1)

    if green_prev > 50 and green_now > 50:
        canopy_shift = round(float(((green_now - green_prev) / green_prev) * 100.0), 1)
    else:
        canopy_shift = 0.0

    area_rounded = round(float(area_km2), 1)
    img_baseline = fast_array_to_png(ndvi_baseline, bbox, geom_geojson if has_polygon else None)
    img_target = fast_array_to_png(ndvi_target, bbox, geom_geojson if has_polygon else None)

    # 4. Store spatial footprint & results in PostGIS cache
    try:
        footprint = poly_geom if poly_geom is not None else box(*bbox)
        entry = TownAnalysisCache(
            query_key=query_clean,
            location_name=display_name,
            baseline_year=req.baseline_year,
            target_year=req.target_year,
            greenery_coverage_pct=greenery_pct,
            canopy_change_pct=canopy_shift,
            total_area_analyzed_km2=area_rounded,
            image_baseline_b64=img_baseline,
            image_target_b64=img_target,
            geom=from_shape(footprint, srid=4326),
        )
        db.add(entry)
        await db.commit()
    except Exception:
        await db.rollback()
    return {
        "location_name": display_name,
        "bbox": bbox,
        "baseline_year": req.baseline_year,
        "target_year": req.target_year,
        "greenery_coverage_pct": greenery_pct,
        "canopy_change_pct": canopy_shift,
        "total_area_analyzed_km2": area_rounded,
        "images": {
            str(req.baseline_year): img_baseline,
            str(req.target_year): img_target,
        },
    }
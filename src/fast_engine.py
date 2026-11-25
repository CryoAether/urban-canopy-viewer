from __future__ import annotations

import asyncio
import base64
import io
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple

import cv2
import numpy as np
import planetary_computer as pc
from PIL import Image
from pyproj import Transformer
from pystac_client import Client
import rasterio
from rasterio.enums import Resampling
import rasterio.features
from rasterio.windows import from_bounds
from scipy.ndimage import distance_transform_edt

from src.constants import CLOUD_COVER_LT, MAX_CANDIDATE_SCENES, RASTER_SHAPE

CATALOG = "https://planetarycomputer.microsoft.com/api/stac/v1"

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "32000000",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "CPL_VSIL_CURL_TIMEOUT": "25",
}


def read_band_window(
    url: str,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    target_shape: Tuple[int, int] = RASTER_SHAPE,
) -> np.ndarray:
    try:
        with rasterio.Env(**GDAL_ENV):
            with rasterio.open(url) as src:
                tfm = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                minx, miny = tfm.transform(min_lon, min_lat)
                maxx, maxy = tfm.transform(max_lon, max_lat)

                left, right = min(minx, maxx), max(minx, maxx)
                bottom, top = min(miny, maxy), max(miny, maxy)

                if (
                    right <= src.bounds.left
                    or left >= src.bounds.right
                    or top <= src.bounds.bottom
                    or bottom >= src.bounds.top
                ):
                    return np.full(target_shape, np.nan, dtype=np.float32)

                window = from_bounds(left, bottom, right, top, src.transform)
                arr = src.read(
                    1,
                    window=window,
                    out_shape=target_shape,
                    resampling=Resampling.bilinear,
                    fill_value=np.nan,
                    boundless=True,
                )
                return arr.astype(np.float32)
    except Exception:
        return np.full(target_shape, np.nan, dtype=np.float32)


def process_single_scene(
    item_dict: dict,
    bbox: list[float],
    year: int,
    shape: Tuple[int, int] = RASTER_SHAPE,
) -> np.ndarray:
    min_lon, min_lat, max_lon, max_lat = bbox
    b4_url = item_dict["assets"]["B04"]["href"]
    b8_url = item_dict["assets"]["B08"]["href"]
    scl_url = item_dict["assets"]["SCL"]["href"]

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_red = ex.submit(read_band_window, b4_url, min_lon, min_lat, max_lon, max_lat, shape)
        f_nir = ex.submit(read_band_window, b8_url, min_lon, min_lat, max_lon, max_lat, shape)
        f_scl = ex.submit(read_band_window, scl_url, min_lon, min_lat, max_lon, max_lat, shape)

        red = f_red.result()
        nir = f_nir.result()
        scl = f_scl.result()

    if np.all(np.isnan(scl)):
        return np.full(shape, np.nan, dtype=np.float32)

    if year >= 2022:
        red = np.clip(red - 1000.0, 0, None)
        nir = np.clip(nir - 1000.0, 0, None)

    red = red * 0.0001
    nir = nir * 0.0001

    ndvi = (nir - red) / (nir + red + 1e-6)

    is_cloud_or_nodata = np.isnan(scl) | np.isin(scl, [0, 1, 3, 8, 9, 10, 11])
    is_water = (scl == 6) | (nir < 0.05)

    ndvi[is_water] = -1.0
    ndvi[is_cloud_or_nodata | np.isnan(ndvi)] = np.nan
    return ndvi


async def get_fast_year_composite(
    bbox: list[float],
    year: int,
    shape: Tuple[int, int] = RASTER_SHAPE,
    geom_geojson: dict | None = None,
) -> np.ndarray:
    loop = asyncio.get_event_loop()

    # Use peak summer window per hemisphere to avoid seasonal rain/superbloom bias
    center_lat = (bbox[1] + bbox[3]) / 2.0
    if center_lat >= 0:
        date_range = f"{year}-06-01/{year}-09-30"
    else:
        date_range = f"{year - 1}-12-01/{year}-03-31"

    def _search():
        client = Client.open(CATALOG)
        search = client.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": CLOUD_COVER_LT}},
        )
        items = list(search.items())
        seen = set()
        uniq = []
        for it in items:
            if it.id not in seen:
                seen.add(it.id)
                uniq.append(it)

        # Group by both MGRS tile and relative orbital pass so every swath is sampled
        by_orbit: dict[tuple, list] = {}
        for it in uniq:
            tile_id = it.properties.get("s2:mgrs_tile", "unknown")
            rel_orbit = it.properties.get("sat:relative_orbit", 0)
            by_orbit.setdefault((tile_id, rel_orbit), []).append(it)

        for orbit_items in by_orbit.values():
            orbit_items.sort(key=lambda x: x.properties.get("eo:cloud_cover", 100))

        interleaved = []
        idx = 0
        while len(interleaved) < MAX_CANDIDATE_SCENES:
            added = False
            for orbit_items in by_orbit.values():
                if idx < len(orbit_items):
                    interleaved.append(orbit_items[idx])
                    added = True
                    if len(interleaved) >= MAX_CANDIDATE_SCENES:
                        break
            if not added:
                break
            idx += 1

        return interleaved, max(1, len(by_orbit))

    items, num_orbits = await loop.run_in_executor(None, _search)
    if not items:
        return np.full(shape, np.nan, dtype=np.float32)

    target_mask = np.ones(shape, dtype=bool)
    if geom_geojson and geom_geojson.get("type") in ["Polygon", "MultiPolygon"]:
        try:
            h, w = shape
            inv_transform = rasterio.transform.from_bounds(
                bbox[0], bbox[1], bbox[2], bbox[3], w, h
            )
            rasterized = rasterio.features.rasterize(
                [(geom_geojson, 1)],
                out_shape=shape,
                transform=inv_transform,
                fill=0,
                dtype=np.uint8,
            ).astype(bool)
            if np.any(rasterized):
                target_mask = rasterized
        except Exception:
            pass

    target_pixels = max(1, int(np.count_nonzero(target_mask)))

    def _progressive_composite():
        valid_arrays: list[np.ndarray] = []
        coverage_tracker = np.zeros(shape, dtype=bool)
        batch_size = max(6, num_orbits * 2)

        for i in range(0, len(items), batch_size):
            batch = [pc.sign(it).to_dict() for it in items[i : i + batch_size]]
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(process_single_scene, item, bbox, year, shape)
                    for item in batch
                ]
                arrays = [f.result() for f in futures]

            for arr in arrays:
                finite = np.isfinite(arr)
                if np.any(finite):
                    valid_arrays.append(arr)
                    coverage_tracker |= finite

            filled_in_target = np.count_nonzero(coverage_tracker & target_mask)
            if (filled_in_target / target_pixels) >= 0.995 and len(valid_arrays) >= num_orbits * 2:
                break

        if not valid_arrays:
            return np.full(shape, np.nan, dtype=np.float32)

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            composite = np.nanmedian(np.stack(valid_arrays, axis=0), axis=0).astype(np.float32)

        return composite

    return await loop.run_in_executor(None, _progressive_composite)


def fast_array_to_png(
    arr: np.ndarray,
    bbox: list[float] | None = None,
    geom_geojson: dict | None = None,
) -> str:
    h, w = arr.shape
    arr = arr.copy()

    # Fill only true unobserved NaN gaps (observed water is -1.0, so it won't be smeared)
    nan_mask = np.isnan(arr)
    if np.any(nan_mask) and not np.all(nan_mask):
        _, nearest_idx = distance_transform_edt(nan_mask, return_indices=True)
        arr = arr[tuple(nearest_idx)]

    rgba = np.full((h, w, 4), [238, 235, 227, 255], dtype=np.uint8)

    water_mask = np.isnan(arr) | (arr < 0.08)
    rgba[water_mask] = [219, 234, 244, 255]

    veg_mask = np.isfinite(arr) & (arr >= 0.36)
    if np.any(veg_mask):
        norm = np.clip((arr[veg_mask] - 0.36) / (0.78 - 0.36), 0.0, 1.0)
        r = (104 - (104 - 25) * norm).astype(np.uint8)
        g = (187 - (187 - 78) * norm).astype(np.uint8)
        b = (89 - (89 - 40) * norm).astype(np.uint8)
        rgba[veg_mask, 0] = r
        rgba[veg_mask, 1] = g
        rgba[veg_mask, 2] = b
        rgba[veg_mask, 3] = 255

    if geom_geojson and bbox and geom_geojson.get("type") in ["Polygon", "MultiPolygon"]:
        try:
            min_lon, min_lat, max_lon, max_lat = bbox
            inv_transform = rasterio.transform.from_bounds(min_lon, min_lat, max_lon, max_lat, w, h)
            inside_mask = rasterio.features.rasterize(
                [(geom_geojson, 1)],
                out_shape=(h, w),
                transform=inv_transform,
                fill=0,
                dtype=np.uint8,
            ).astype(bool)

            inside_count = np.count_nonzero(inside_mask)
            if 0.01 * (h * w) < inside_count < 0.99 * (h * w):
                outside = ~inside_mask
                rgba[outside, :3] = (rgba[outside, :3] * 0.5 + 238 * 0.5).astype(np.uint8)

                contours, _ = cv2.findContours(
                    inside_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                cv2.drawContours(rgba, contours, -1, (0, 180, 216, 255), 3)
                cv2.drawContours(rgba, contours, -1, (255, 255, 255, 255), 1)
        except Exception:
            pass

    img = Image.fromarray(rgba)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"
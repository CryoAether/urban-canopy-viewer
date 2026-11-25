import asyncio
from pathlib import Path
import httpx
import numpy as np
from PIL import Image

from src.fast_engine import get_fast_year_composite, fast_array_to_png
from src.constants import RASTER_SHAPE

# 10m Ultra-Crisp Dimensions (2:1 Widescreen Aspect)
TARGET_SHAPE = (600, 1200)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "demo_assets"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


async def geocode_meadow_valley():
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": "Meadow Valley, California", "format": "json", "polygon_geojson": 1, "limit": 1}
    headers = {"User-Agent": "DeforestationViewer/2.0"}
    
    async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        item = data[0]
        return float(item["lat"]), float(item["lon"]), item.get("geojson")


async def main():
    print("1. Geocoding Meadow Valley...")
    lat, lon, geom_geojson = await geocode_meadow_valley()

    # Calculate 2:1 Widescreen Bounding Box (~14km x 7km)
    delta_lat = 4.5 / 111.0
    delta_lon = (delta_lat * 2.0) / np.cos(np.radians(lat))
    bbox = [lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat]

    print("2. Fetching Sentinel-2 (2020 & 2024) at 10m resolution...")
    task_2020 = get_fast_year_composite(bbox, 2020, shape=TARGET_SHAPE)
    task_2024 = get_fast_year_composite(bbox, 2024, shape=TARGET_SHAPE)
    
    ndvi_2020, ndvi_2024 = await asyncio.gather(task_2020, task_2024)

    print("3. Rendering and saving PNG assets...")
    
    # Save 2020
    b64_2020 = fast_array_to_png(ndvi_2020, bbox, geom_geojson)
    img_data_2020 = b64_2020.split(",")[1]
    with open(OUTPUT_DIR / "meadow_valley_2020.png", "wb") as f:
        import base64
        f.write(base64.b64decode(img_data_2020))

    # Save 2024
    b64_2024 = fast_array_to_png(ndvi_2024, bbox, geom_geojson)
    img_data_2024 = b64_2024.split(",")[1]
    with open(OUTPUT_DIR / "meadow_valley_2024.png", "wb") as f:
        import base64
        f.write(base64.b64decode(img_data_2024))

    print(f"Saved to: {OUTPUT_DIR}/meadow_valley_2020.png")
    print(f"Saved to: {OUTPUT_DIR}/meadow_valley_2024.png")


if __name__ == "__main__":
    asyncio.run(main())
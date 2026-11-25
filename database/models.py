from __future__ import annotations

from geoalchemy2 import Geometry
from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, func

from database.connection import Base


class TownAnalysisCache(Base):
    __tablename__ = "town_analysis_cache"

    id = Column(Integer, primary_key=True, index=True)
    query_key = Column(String(255), nullable=False, index=True)
    location_name = Column(String(255), nullable=False)
    baseline_year = Column(Integer, nullable=False, default=2020)
    target_year = Column(Integer, nullable=False, default=2024)

    greenery_coverage_pct = Column(Float, nullable=False)
    canopy_change_pct = Column(Float, nullable=False)
    total_area_analyzed_km2 = Column(Float, nullable=False)

    image_baseline_b64 = Column(Text, nullable=False)
    image_target_b64 = Column(Text, nullable=False)

    # Spatial footprint (EPSG:4326) indexed with GiST for fast spatial lookups
    geom = Column(Geometry(geometry_type="GEOMETRY", srid=4326), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_town_cache_geom", "geom", postgresql_using="gist"),
    )
# Target Ground Sampling & Matrix Resolution
# 10m crisp: (500, 1000)
# 15m balanced: (350, 700)
# 20m fast: (250, 500)
RASTER_HEIGHT = 600
RASTER_WIDTH = 1200
RASTER_SHAPE = (RASTER_HEIGHT, RASTER_WIDTH)

# Pull up to 40 candidate scenes across the full year, but stop streaming early once filled
MAX_CANDIDATE_SCENES = 40
CLOUD_COVER_LT = 65
"""Analysis grid in Web Mercator so overlays line up exactly with Leaflet maps.

Pixel size is scaled by 1/cos(latitude) so each pixel covers ~10 m on the ground.
"""
import math
from dataclasses import dataclass

R = 6378137.0


def lonlat_to_merc(lon, lat):
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return x, y


def merc_to_lonlat(x, y):
    lon = math.degrees(x / R)
    lat = math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
    return lon, lat


@dataclass(frozen=True)
class Grid:
    x0: float          # left edge (EPSG:3857 metres)
    y0: float          # top edge
    pixel: float       # pixel size in EPSG:3857 metres
    width: int
    height: int
    ground_resolution_m: float
    crs: str = "EPSG:3857"

    @property
    def pixel_area_m2(self):
        return self.ground_resolution_m ** 2

    def affine_params(self):
        """(a, b, c, d, e, f) for affine.Affine / rasterio."""
        return (self.pixel, 0.0, self.x0, 0.0, -self.pixel, self.y0)

    def pixel_to_lonlat(self, row, col):
        x = self.x0 + (col + 0.5) * self.pixel
        y = self.y0 - (row + 0.5) * self.pixel
        return merc_to_lonlat(x, y)

    def bounds_lonlat(self):
        """[[south, west], [north, east]] in the shape Leaflet expects."""
        west, north = merc_to_lonlat(self.x0, self.y0)
        east, south = merc_to_lonlat(self.x0 + self.width * self.pixel,
                                     self.y0 - self.height * self.pixel)
        return [[south, west], [north, east]]


def build_grid(bbox, ground_resolution_m=10.0):
    min_lon, min_lat, max_lon, max_lat = bbox
    x0, y_min = lonlat_to_merc(min_lon, min_lat)
    x1, y_max = lonlat_to_merc(max_lon, max_lat)
    scale = 1 / math.cos(math.radians((min_lat + max_lat) / 2))
    pixel = ground_resolution_m * scale
    width = max(1, math.ceil((x1 - x0) / pixel))
    height = max(1, math.ceil((y_max - y_min) / pixel))
    return Grid(x0=x0, y0=y_max, pixel=pixel, width=width, height=height,
                ground_resolution_m=ground_resolution_m)

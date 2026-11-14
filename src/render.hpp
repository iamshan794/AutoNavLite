// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — server-side raster products rendered from the DEM (PNG tiles, EPSG:4326 grid)
#pragma once
#include <string>

#include "dem.hpp"

namespace autonav {

struct RenderOptions {
  std::string kind = "hillshade";  // hillshade | slope | contours
  double sun_az_deg = 315, sun_el_deg = 35;
  bool shadows = true;
  double max_slope_deg = 25;
  double contour_interval_m = 0;  // 0 = auto from zoom
};

// Renders a 256x256 RGBA tile (Trek / Leaflet EPSG:4326 scheme: z0 = 2x1 tiles).
// Returns PNG bytes, or empty string if tile is out of range.
std::string render_tile(const DemStore& dem, int z, int row, int col, const RenderOptions& o);
// Colour-coded categorical tile (terrain classes) using each grid's class colours.
std::string render_class_tile(const DemStore& classes, int z, int row, int col, int alpha = 150);

}  // namespace autonav

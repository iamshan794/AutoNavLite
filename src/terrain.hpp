// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — Level-1 terrain classes (landforms) from the DEM.
//
// Two-scale Topographic Position Index after Weiss (2001), "Topographic Position and Landforms
// Analysis" (ESRI poster), as popularised by Jenness (2006): TPI = z - mean(z in a box), computed
// at a small (local) and large (regional) radius, standardised, and combined with slope.
#pragma once
#include "dem.hpp"

namespace autonav {

enum Landform : int16_t {
  kValley = 1,      // valley / channel / incised drainage (low at the local scale)
  kBasin = 2,       // crater floor / basin (low at the regional scale, locally flat)
  kPlain = 3,       // flat plain
  kSlope = 4,       // open slope / crater wall
  kUpland = 5,      // mesa / upland plateau
  kRidge = 6,       // ridge / crater rim (high at the local scale)
  kPeak = 7,        // peak / high ridge (high at both scales)
};

// Landform grid with the same geometry as `dem` (categorical, level 1).
DemGrid compute_landforms(const DemGrid& dem, double small_radius_m = 1500,
                          double large_radius_m = 7500, double plain_slope_deg = 3.0);
// One landform grid per elevation grid in the store.
DemStore compute_landforms(const DemStore& dem);
// Run-once cache: loads <dir>/<dem id>_landform.{json,img}; grids that are missing (or `rebuild`)
// are computed and saved there. `built` receives the number computed.
DemStore load_or_build_landforms(const DemStore& dem, const std::string& dir, bool rebuild,
                                 int* built = nullptr, std::string* err = nullptr);

}  // namespace autonav

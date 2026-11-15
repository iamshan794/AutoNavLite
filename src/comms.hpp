// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — communication dead spots from terrain masking.
//
// Relay orbiters (MRO, MAVEN, TGO, Mars Express) are only usable above a minimum elevation angle
// (~10 deg is assumed here). Crater walls, canyon floors and the foot of large mounds block part of the
// sky, so fewer relay passes reach the ground there. For every DEM cell the horizon angle is traced
// in `azimuths` directions (with Mars curvature); the fraction of directions that stay open above
// `mask_deg` gives the class. This is a geometric proxy for relay-pass loss, not a link budget.
#pragma once
#include "dem.hpp"

namespace autonav {

enum CommClass : int16_t { kCommOpen = 1, kCommReduced = 2, kCommPoor = 3, kCommDead = 4 };

struct CommOptions {
  double mask_deg = 10.0;        // minimum usable relay elevation
  double max_range_m = 50000.0;  // 8 km of relief cannot mask 10 deg beyond ~45 km
  int azimuths = 16;
};

// Fraction of azimuths whose horizon stays below the mask, for the cell at (row, col) of `g`.
// Rays leaving `g` continue on the other grids of `dem` (e.g. the global DEM).
double comm_open_fraction(const DemStore& dem, const DemGrid& g, int row, int col,
                          const CommOptions& o = {});
// Categorical coverage grid (level 1, same geometry as `g`); classes carry a planner `comm` loss.
DemGrid compute_comm_coverage(const DemStore& dem, const DemGrid& g, const CommOptions& o = {});
// Run-once cache under `dir` (like load_or_build_landforms).
DemStore load_or_build_comms(const DemStore& dem, const std::string& dir, bool rebuild,
                             int* built = nullptr, std::string* err = nullptr);

}  // namespace autonav

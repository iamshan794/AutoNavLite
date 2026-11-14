// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — risk-aware A* route planner over the cached DEM (+ optional thermal inertia).
#pragma once
#include <string>
#include <vector>

#include "dem.hpp"

namespace autonav {

// Shortest : 3D distance, 45 deg physical limit.
// Safe     : slope-penalised, hard slope limit, soft-soil (low TI) sand-trap penalty.
// Eva      : astronaut walking time (Tobler), hard slope limit.
// Wear     : "wheel saver" - minimises the wear index (rough/rocky ground, slope load,
//            turning scrub); accepts longer routes to protect wheels.
enum class RouteProfile { Shortest, Safe, Eva, Wear };
RouteProfile parse_profile(const std::string& s);
const char* profile_name(RouteProfile p);
const char* profile_label(RouteProfile p);

struct RouteRequest {
  double lat0, lon0, lat1, lon1;
  RouteProfile profile = RouteProfile::Safe;
  double max_slope_deg = 25.0;     // hard limit (Safe/Eva/Wear); Shortest uses 45
  int max_grid = 400;              // max cells per side (bounds CPU / RAM)
  double rover_m_per_sol = 150.0;  // drive budget per sol (MSL/M2020-class)
  double odo_drift_frac = 0.02;    // dead-reckoning drift (fraction of distance)
  double reloc_sigma_m = 50.0;     // force re-localization when 1-sigma exceeds this
  // Weight of relay-comms loss (comm dead spots) in the Safe (x1) and EVA (x2) costs; 0 = ignore.
  double comm_weight = 1.0;
  // Optional reference trajectory (e.g. a historic rover traverse), lat/lon pairs. plan_compare()
  // scores it with the same telemetry/wear model as the planned routes.
  std::vector<std::pair<double, double>> reference;
  std::string reference_id = "historic", reference_label = "Historic traverse";
};

struct RoutePoint {
  double lat, lon, elev, dist_m, slope_deg, sigma_m;
  double wear;  // local wear factor (1 = benign ground)
};

struct RouteResult {
  bool ok = false;
  std::string error, id, label;
  RouteProfile profile = RouteProfile::Safe;
  bool alternative = false;
  bool reference = false;  // evaluated reference trajectory, not planned
  double overlap = 0;  // fraction of cells shared with the primary route (alternatives)
  std::vector<RoutePoint> points;
  std::vector<int> reloc_stops;  // indices into points
  double dist_m = 0, dist3d_m = 0, ascent_m = 0, descent_m = 0;
  double max_slope_deg = 0, mean_slope_deg = 0;
  double eva_hours = 0, rover_sols = 0, rover_energy_wh = 0;
  // Wheel-wear telemetry (identical model for every profile so routes are comparable).
  double wear_index = 0;  // wear-equivalent km (1 km of smooth, flat, benign driving = 1)
  double turn_deg = 0;    // accumulated heading change
  double rough_km = 0;    // distance over rough terrain (DEM curvature)
  double rock_km = 0;     // distance over rocky / high thermal-inertia ground
  double soft_km = 0;     // distance over soft, low thermal-inertia ground (sand-trap risk)
  bool ti_available = false;
  bool material_available = false;  // a mapped terrain-class layer refined rock/soft
  bool comm_available = false;      // relay comm-coverage grid used
  double comm_dead_km = 0;          // distance through comm dead spots
  double comm_poor_km = 0;          // distance with poor or worse relay coverage (incl. dead)
  double cell_m = 0, dem_res_m = 0;
  int grid_w = 0, grid_h = 0, expanded = 0;
};

// `material`: optional categorical terrain-class store; each class's ClassInfo rock/soft effect
// (from the layer JSON "planner" table) is applied to the cost grid.
// `comms`: optional relay comm-coverage store (ClassInfo::comm loss per class, see comms.hpp).
RouteResult plan_route(const DemStore& dem, const RouteRequest& req, const DemStore* ti = nullptr,
                       const DemStore* material = nullptr, const DemStore* comms = nullptr);

// Plans every profile in `profiles` on one shared grid, plus `alternatives` diverse
// alternatives of the first profile (penalty method, like map apps' "alternative routes").
std::vector<RouteResult> plan_compare(const DemStore& dem, const RouteRequest& req,
                                      const std::vector<RouteProfile>& profiles, int alternatives,
                                      const DemStore* ti = nullptr,
                                      const DemStore* material = nullptr,
                                      const DemStore* comms = nullptr);

// Tobler hiking speed (km/h) scaled for a pressurised suit; slope as rise/run.
double eva_speed_kmh(double grade);

}  // namespace autonav

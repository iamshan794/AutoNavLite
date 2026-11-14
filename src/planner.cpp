// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "planner.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <queue>

namespace autonav {

RouteProfile parse_profile(const std::string& s) {
  if (s == "shortest") return RouteProfile::Shortest;
  if (s == "eva") return RouteProfile::Eva;
  if (s == "wear" || s == "wheelsaver") return RouteProfile::Wear;
  return RouteProfile::Safe;
}

const char* profile_name(RouteProfile p) {
  switch (p) {
    case RouteProfile::Shortest: return "shortest";
    case RouteProfile::Eva: return "eva";
    case RouteProfile::Wear: return "wear";
    default: return "safe";
  }
}

const char* profile_label(RouteProfile p) {
  switch (p) {
    case RouteProfile::Shortest: return "Shortest";
    case RouteProfile::Eva: return "Astronaut EVA";
    case RouteProfile::Wear: return "Wheel saver";
    default: return "Safe rover";
  }
}

double eva_speed_kmh(double grade) {
  // Tobler (1993) hiking function, scaled by 0.5 for pressurised-suit mobility
  // (Apollo EVA traverse speeds were typically 1-3 km/h).
  return 0.5 * 6.0 * std::exp(-3.5 * std::fabs(grade + 0.05));
}

namespace {

constexpr int DX[8] = {1, -1, 0, 0, 1, 1, -1, -1};
constexpr int DY[8] = {0, 0, 1, -1, 1, -1, 1, -1};
constexpr double kTurnWearCells = 0.6;  // wear of a 45 deg heading change, in cell-lengths

double turn_between(int a, int b) {
  if (a < 0 || b < 0) return 0;
  double t = std::fabs(std::atan2(DY[a], DX[a]) - std::atan2(DY[b], DX[b])) / kDeg;
  return t > 180 ? 360 - t : t;
}

int dir_of_step(int W, int from, int to) {
  int dx = to % W - from % W, dy = to / W - from / W;
  for (int k = 0; k < 8; ++k)
    if (DX[k] == dx && DY[k] == dy) return k;
  return -1;
}

// Thermal inertia classes (MGS TES, J m^-2 K^-1 s^-1/2). TI > ~400 is rock/bedrock dominated,
// TI < ~150 is fine dust / unconsolidated material (Christensen et al. 2001; Putzig & Mellon 2007).
double rockiness(double ti) { return std::isnan(ti) ? 0 : std::clamp((ti - 300.0) / 200.0, 0.0, 1.0); }
double softness(double ti) { return std::isnan(ti) ? 0 : std::clamp((150.0 - ti) / 100.0, 0.0, 1.0); }

struct Grid {
  int W = 0, H = 0;
  double cell = 0, dlat = 0, dlon = 0, lat_top = 0, lon_left = 0, res_m = 0;
  std::vector<float> z, slope, rough, rock, soft, comm;
  bool ti = false, material = false, comms = false;
  int s = 0, g = 0;
  double lat(int y) const { return lat_top - (y + 0.5) * dlat; }
  double lon(int x) const { return lon_left + (x + 0.5) * dlon; }
  size_t N() const { return static_cast<size_t>(W) * H; }
};

// Mapped terrain classes (scripts/terrain_map.py) refine the TI proxies with each class's
// planner effect from the layer JSON: rock = wheel damage / roughness, soft = sinkage risk.
void apply_terrain_class(const ClassInfo& c, float& rock, float& soft) {
  if (c.rock >= 0) rock = std::max(rock, static_cast<float>(c.rock));
  if (c.soft >= 0) soft = std::max(soft, static_cast<float>(c.soft));
}

bool build_grid(const DemStore& dem, const DemStore* ti, const DemStore* material,
                const DemStore* comms, const RouteRequest& rq, Grid& G, std::string& err) {
  const double lon1u = rq.lon0 + wrap180(rq.lon1 - rq.lon0);  // unwrap across 180
  const double coslat = std::max(0.05, std::cos(0.5 * (rq.lat0 + rq.lat1) * kDeg));
  G.res_m = std::max(dem.best_res_m(rq.lat0, rq.lon0), dem.best_res_m(rq.lat1, rq.lon1));
  if (G.res_m <= 0) {
    err = "no DEM coverage at route endpoints";
    return false;
  }
  const double m_per_deg = kMarsRadiusM * kDeg;
  double lat_lo = std::min(rq.lat0, rq.lat1), lat_hi = std::max(rq.lat0, rq.lat1);
  double lon_lo = std::min(rq.lon0, lon1u), lon_hi = std::max(rq.lon0, lon1u);
  for (auto& [la, lo] : rq.reference) {
    double lu = rq.lon0 + wrap180(lo - rq.lon0);
    lat_lo = std::min(lat_lo, la);
    lat_hi = std::max(lat_hi, la);
    lon_lo = std::min(lon_lo, lu);
    lon_hi = std::max(lon_hi, lu);
  }
  double span_y = (lat_hi - lat_lo) * m_per_deg;
  double span_x = (lon_hi - lon_lo) * m_per_deg * coslat;
  double pad = std::max({0.25 * std::max(span_x, span_y), 6 * G.res_m, 500.0});
  double ext_x = span_x + 2 * pad, ext_y = span_y + 2 * pad;
  G.cell = std::max(G.res_m * 0.5, std::max(ext_x, ext_y) / rq.max_grid);
  G.W = std::max(3, static_cast<int>(std::ceil(ext_x / G.cell)));
  G.H = std::max(3, static_cast<int>(std::ceil(ext_y / G.cell)));
  G.dlat = G.cell / m_per_deg;
  G.dlon = G.cell / (m_per_deg * coslat);
  G.lat_top = lat_hi + pad / m_per_deg;
  G.lon_left = lon_lo - pad / (m_per_deg * coslat);
  const int W = G.W, H = G.H;
  const size_t N = G.N();
  G.z.resize(N);
  G.slope.resize(N);
  G.rough.resize(N);
  G.rock.assign(N, 0.f);
  G.soft.assign(N, 0.f);
  G.comm.assign(N, 0.f);
  for (int y = 0; y < H; ++y)
    for (int x = 0; x < W; ++x) {
      double e = dem.elev(G.lat(y), G.lon(x));
      G.z[y * W + x] = e > kNoData ? static_cast<float>(e) : std::numeric_limits<float>::quiet_NaN();
      if (ti) {
        double t = ti->elev(G.lat(y), G.lon(x));
        if (t > kNoData) {
          G.ti = true;
          G.rock[y * W + x] = static_cast<float>(rockiness(t));
          G.soft[y * W + x] = static_cast<float>(softness(t));
        }
      }
      if (material) {
        const DemGrid* cg = nullptr;
        int c = material->class_at(G.lat(y), G.lon(x), &cg);
        if (cg) {
          auto it = cg->classes.find(c);
          if (it != cg->classes.end()) {
            G.material = true;
            apply_terrain_class(it->second, G.rock[y * W + x], G.soft[y * W + x]);
          }
        }
      }
      if (comms) {
        const DemGrid* cg = nullptr;
        int c = comms->class_at(G.lat(y), G.lon(x), &cg);
        if (cg) {
          auto it = cg->classes.find(c);
          if (it != cg->classes.end() && it->second.comm >= 0) {
            G.comms = true;
            G.comm[y * W + x] = static_cast<float>(it->second.comm);
          }
        }
      }
    }
  auto Z = [&](int yy, int xx) { return G.z[std::clamp(yy, 0, H - 1) * W + std::clamp(xx, 0, W - 1)]; };
  for (int y = 0; y < H; ++y)
    for (int x = 0; x < W; ++x) {
      double gx = (Z(y, x + 1) - Z(y, x - 1)) / (2 * G.cell);
      double gy = (Z(y + 1, x) - Z(y - 1, x)) / (2 * G.cell);
      G.slope[y * W + x] = static_cast<float>(std::atan(std::hypot(gx, gy)) / kDeg);
      // Detrended roughness: Laplacian (zero on any plane), dimensionless, saturates at 1.
      double lap = std::fabs(Z(y, x + 1) + Z(y, x - 1) + Z(y + 1, x) + Z(y - 1, x) - 4 * Z(y, x)) / G.cell;
      G.rough[y * W + x] = static_cast<float>(std::min(1.0, lap / 0.2));
    }
  auto to_cell = [&](double lat, double lon) {
    int cx = std::clamp(static_cast<int>((lon - G.lon_left) / G.dlon), 0, W - 1);
    int cy = std::clamp(static_cast<int>((G.lat_top - lat) / G.dlat), 0, H - 1);
    return cy * W + cx;
  };
  G.s = to_cell(rq.lat0, rq.lon0);
  G.g = to_cell(rq.lat1, lon1u);
  return true;
}

// Local wear multiplier for traversing between two adjacent cells.
double wear_factor(const Grid& G, int i, int j, double limit) {
  double q = std::max(G.slope[i], G.slope[j]) / limit;
  double r = 0.5 * (G.rough[i] + G.rough[j]);
  double rock = 0.5 * (G.rock[i] + G.rock[j]);
  double soft = 0.5 * (G.soft[i] + G.soft[j]);
  return 1.0 + 2.0 * q * q + 3.0 * r + 2.0 * rock + 0.5 * soft;
}

double hard_limit(RouteProfile p, double max_slope) {
  return p == RouteProfile::Shortest ? 45.0 : max_slope;
}

std::vector<int> astar(const Grid& G, RouteProfile prof, double limit, double comm_w,
                       const std::vector<float>* pen, int& expanded) {
  const int W = G.W, H = G.H;
  const size_t N = G.N();
  const bool heading = prof == RouteProfile::Wear;  // turning scrub needs heading in the state
  const size_t D = heading ? 8 : 1;
  const size_t S = N * D + 1, start = N * D;
  const double hard = hard_limit(prof, limit);
  const double vmax = eva_speed_kmh(-0.05);
  const int gx = G.g % W, gy = G.g / W;
  auto heuristic = [&](int c) {
    double d = std::hypot((c % W - gx) * G.cell, (c / W - gy) * G.cell);
    return prof == RouteProfile::Eva ? d / 1000.0 / vmax : d;
  };
  auto cell_of = [&](size_t s) { return s == start ? G.s : static_cast<int>(s / D); };
  auto dir_of = [&](size_t s) { return (s == start || !heading) ? -1 : static_cast<int>(s % D); };

  std::vector<double> gc(S, std::numeric_limits<double>::infinity());
  std::vector<int64_t> par(S, -1);
  std::vector<char> closed(S, 0);
  using QE = std::pair<double, size_t>;
  std::priority_queue<QE, std::vector<QE>, std::greater<>> open;
  gc[start] = 0;
  open.push({heuristic(G.s), start});
  size_t goal_state = S;
  expanded = 0;
  while (!open.empty()) {
    size_t st = open.top().second;
    open.pop();
    if (closed[st]) continue;
    closed[st] = 1;
    ++expanded;
    int i = cell_of(st);
    if (i == G.g) {
      goal_state = st;
      break;
    }
    int x = i % W, y = i / W, pd = dir_of(st);
    for (int k = 0; k < 8; ++k) {
      int nx = x + DX[k], ny = y + DY[k];
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      int j = ny * W + nx;
      size_t ns = static_cast<size_t>(j) * D + (heading ? k : 0);
      if (closed[ns] || std::isnan(G.z[j])) continue;
      double terrain = std::max(G.slope[i], G.slope[j]);
      if (terrain > hard && j != G.g) continue;
      double d = (k < 4 ? 1.0 : std::sqrt(2.0)) * G.cell;
      double dz = G.z[j] - G.z[i];
      double d3 = std::hypot(d, dz);
      double c;
      switch (prof) {
        case RouteProfile::Shortest: c = d3; break;
        case RouteProfile::Eva:
          // Astronauts must stay in contact: comm dead spots weigh twice as much as for the rover.
          c = d3 / 1000.0 / eva_speed_kmh(dz / d) * (1.0 + 2.0 * comm_w * 0.5 * (G.comm[i] + G.comm[j]));
          break;
        case RouteProfile::Wear:
          c = d3 * wear_factor(G, i, j, limit) + G.cell * kTurnWearCells * turn_between(pd, k) / 45.0;
          break;
        default: {
          double q = terrain / limit, soft = 0.5 * (G.soft[i] + G.soft[j]);
          c = d3 * (1.0 + 4.0 * q * q + 3.0 * soft + comm_w * 0.5 * (G.comm[i] + G.comm[j]));
        }
      }
      if (pen) c *= (*pen)[j];
      if (gc[st] + c < gc[ns]) {
        gc[ns] = gc[st] + c;
        par[ns] = static_cast<int64_t>(st);
        open.push({gc[ns] + heuristic(j), ns});
      }
    }
  }
  std::vector<int> cells;
  if (goal_state == S) return cells;
  for (int64_t s = static_cast<int64_t>(goal_state); s >= 0; s = par[s]) cells.push_back(cell_of(s));
  std::reverse(cells.begin(), cells.end());
  return cells;
}

RouteResult telemetry(const Grid& G, const std::vector<int>& cells, const RouteRequest& rq,
                      RouteProfile prof) {
  RouteResult r;
  r.profile = prof;
  r.id = profile_name(prof);
  r.label = profile_label(prof);
  r.cell_m = G.cell;
  r.dem_res_m = G.res_m;
  r.grid_w = G.W;
  r.grid_h = G.H;
  r.ti_available = G.ti;
  r.material_available = G.material;
  r.comm_available = G.comms;
  const int W = G.W;
  const double mass = 1025.0, grav = 3.721, crr = 0.15, eta = 0.25;  // MSL-class rover
  double since_reloc = 0, slope_sum = 0, wear_m = 0;
  int prev_dir = -1;
  for (size_t k = 0; k < cells.size(); ++k) {
    int i = cells[k];
    RoutePoint p{G.lat(i / W), wrap180(G.lon(i % W)), G.z[i], 0, G.slope[i], 0, 1.0};
    if (k == 0) { p.lat = rq.lat0; p.lon = wrap180(rq.lon0); }
    if (k + 1 == cells.size() && cells.size() > 1) { p.lat = rq.lat1; p.lon = wrap180(rq.lon1); }
    if (k > 0) {
      const auto& q = r.points.back();
      int ip = cells[k - 1];
      double d = haversine_m(q.lat, q.lon, p.lat, p.lon);
      double dz = p.elev - q.elev;
      double d3 = std::hypot(d, dz);
      r.dist_m += d;
      r.dist3d_m += d3;
      (dz > 0 ? r.ascent_m : r.descent_m) += std::fabs(dz);
      r.eva_hours += d3 / 1000.0 / eva_speed_kmh(d > 0 ? dz / d : 0);
      r.rover_energy_wh += (mass * grav * crr * d + mass * grav * std::max(0.0, dz)) / eta / 3600.0;
      int dir = dir_of_step(W, ip, i);
      double turn = turn_between(prev_dir, dir);
      prev_dir = dir;
      r.turn_deg += turn;
      p.wear = wear_factor(G, ip, i, rq.max_slope_deg);
      wear_m += d3 * p.wear + G.cell * kTurnWearCells * turn / 45.0;
      if (0.5 * (G.rough[ip] + G.rough[i]) > 0.3) r.rough_km += d / 1000.0;
      if (0.5 * (G.rock[ip] + G.rock[i]) > 0.5) r.rock_km += d / 1000.0;
      if (0.5 * (G.soft[ip] + G.soft[i]) > 0.5) r.soft_km += d / 1000.0;
      double cl = 0.5 * (G.comm[ip] + G.comm[i]);
      if (cl >= 0.5) r.comm_poor_km += d / 1000.0;
      if (cl >= 0.99) r.comm_dead_km += d / 1000.0;
      since_reloc += d;
      p.dist_m = r.dist_m;
    }
    p.sigma_m = rq.odo_drift_frac * since_reloc;
    if (p.sigma_m > rq.reloc_sigma_m) {
      r.reloc_stops.push_back(static_cast<int>(k));
      since_reloc = 0;
    }
    r.max_slope_deg = std::max(r.max_slope_deg, p.slope_deg);
    slope_sum += p.slope_deg;
    r.points.push_back(p);
  }
  if (r.points.size() > 1) r.points[0].wear = r.points[1].wear;
  r.mean_slope_deg = r.points.empty() ? 0 : slope_sum / r.points.size();
  r.rover_sols = r.dist_m / rq.rover_m_per_sol;
  r.wear_index = wear_m / 1000.0;
  r.ok = true;
  return r;
}

// Marks every cell within `radius` cells of the path.
std::vector<char> corridor(const Grid& G, const std::vector<int>& cells, int radius) {
  std::vector<char> m(G.N(), 0);
  for (int c : cells)
    for (int dy = -radius; dy <= radius; ++dy)
      for (int dx = -radius; dx <= radius; ++dx) {
        int x = c % G.W + dx, y = c / G.W + dy;
        if (x >= 0 && y >= 0 && x < G.W && y < G.H) m[y * G.W + x] = 1;
      }
  return m;
}

double overlap_with(const std::vector<char>& corr, const std::vector<int>& cells) {
  if (cells.empty()) return 1;
  size_t n = 0;
  for (int c : cells) n += corr[c];
  return static_cast<double>(n) / cells.size();
}

// Rasterises a lat/lon polyline into an 8-connected cell path (no-data cells dropped).
std::vector<int> rasterize(const Grid& G, const RouteRequest& rq) {
  std::vector<int> cells;
  auto fxy = [&](double lat, double lon, double& fx, double& fy) {
    double lu = rq.lon0 + wrap180(lon - rq.lon0);
    fx = (lu - G.lon_left) / G.dlon - 0.5;
    fy = (G.lat_top - lat) / G.dlat - 0.5;
  };
  auto push = [&](double fx, double fy) {
    int x = std::clamp(static_cast<int>(std::lround(fx)), 0, G.W - 1);
    int y = std::clamp(static_cast<int>(std::lround(fy)), 0, G.H - 1);
    int c = y * G.W + x;
    if (std::isnan(G.z[c])) return;
    if (!cells.empty() && cells.back() == c) return;
    // Drop immediate back-and-forth (A-B-A) jitter from sub-cell wiggles.
    if (cells.size() >= 2 && cells[cells.size() - 2] == c) {
      cells.pop_back();
      return;
    }
    cells.push_back(c);
  };
  for (size_t k = 0; k + 1 < rq.reference.size(); ++k) {
    double x0, y0, x1, y1;
    fxy(rq.reference[k].first, rq.reference[k].second, x0, y0);
    fxy(rq.reference[k + 1].first, rq.reference[k + 1].second, x1, y1);
    int n = std::max(1, static_cast<int>(std::ceil(2 * std::max(std::fabs(x1 - x0), std::fabs(y1 - y0)))));
    for (int i = 0; i <= n; ++i) push(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n);
  }
  return cells;
}

}  // namespace

RouteResult plan_route(const DemStore& dem, const RouteRequest& rq, const DemStore* ti,
                       const DemStore* material, const DemStore* comms) {
  auto v = plan_compare(dem, rq, {rq.profile}, 0, ti, material, comms);
  return v.front();
}

std::vector<RouteResult> plan_compare(const DemStore& dem, const RouteRequest& rq,
                                      const std::vector<RouteProfile>& profiles, int alternatives,
                                      const DemStore* ti, const DemStore* material,
                                      const DemStore* comms) {
  std::vector<RouteResult> out;
  Grid G;
  std::string err;
  if (!build_grid(dem, ti, material, comms, rq, G, err)) {
    RouteResult r;
    r.error = err;
    out.push_back(r);
    return out;
  }
  std::vector<int> primary;
  std::vector<char> primary_corr;
  for (size_t n = 0; n < profiles.size(); ++n) {
    int expanded = 0;
    auto cells = astar(G, profiles[n], rq.max_slope_deg, rq.comm_weight, nullptr, expanded);
    RouteResult r;
    if (cells.empty()) {
      r.profile = profiles[n];
      r.id = profile_name(profiles[n]);
      r.label = profile_label(profiles[n]);
      r.error = "no traversable route within slope limit " +
                std::to_string(static_cast<int>(hard_limit(profiles[n], rq.max_slope_deg))) + " deg";
    } else {
      r = telemetry(G, cells, rq, profiles[n]);
      if (n == 0) {
        r.overlap = 1.0;
        primary = cells;
        primary_corr = corridor(G, cells, 1);
      } else if (!primary.empty()) {
        r.overlap = overlap_with(primary_corr, cells);
      }
    }
    r.expanded = expanded;
    out.push_back(std::move(r));
  }

  if (rq.reference.size() >= 2) {
    auto cells = rasterize(G, rq);
    RouteResult r;
    if (cells.size() >= 2) {
      RouteRequest rr = rq;
      rr.lat0 = rq.reference.front().first;
      rr.lon0 = rq.reference.front().second;
      rr.lat1 = rq.reference.back().first;
      rr.lon1 = rq.reference.back().second;
      r = telemetry(G, cells, rr, RouteProfile::Safe);
      if (!primary.empty()) r.overlap = overlap_with(primary_corr, cells);
    } else {
      r.error = "reference trajectory has no DEM coverage";
    }
    r.reference = true;
    r.id = rq.reference_id;
    r.label = rq.reference_label;
    out.push_back(std::move(r));
  }

  if (primary.empty() || alternatives <= 0) return out;
  // Alternatives: inflate the cost of cells near already-found routes and re-plan, keeping only
  // routes that differ enough (shared corridor < 70 %).
  std::vector<float> pen(G.N(), 1.0f);
  std::vector<std::vector<int>> found{primary};
  auto protect = [&](int c) {
    auto near = [&](int a) { return std::abs(c % G.W - a % G.W) <= 4 && std::abs(c / G.W - a / G.W) <= 4; };
    return near(G.s) || near(G.g);
  };
  for (int a = 1; a <= std::min(alternatives, 3); ++a) {
    for (int attempt = 0; attempt < 4; ++attempt) {
      for (auto& path : found) {
        auto corr = corridor(G, path, 2);
        for (size_t c = 0; c < corr.size(); ++c)
          if (corr[c] && !protect(static_cast<int>(c))) pen[c] *= 1.5f;
      }
      int expanded = 0;
      auto cells = astar(G, profiles[0], rq.max_slope_deg, rq.comm_weight, &pen, expanded);
      if (cells.empty()) break;
      double worst = 0;
      for (auto& path : found) worst = std::max(worst, overlap_with(corridor(G, path, 1), cells));
      if (worst < 0.7) {
        auto r = telemetry(G, cells, rq, profiles[0]);
        r.alternative = true;
        r.overlap = overlap_with(primary_corr, cells);
        r.expanded = expanded;
        r.id = std::string(profile_name(profiles[0])) + "-alt" + std::to_string(a);
        r.label = std::string(profile_label(profiles[0])) + " · alternative " + std::to_string(a);
        found.push_back(cells);
        out.push_back(std::move(r));
        break;
      }
    }
  }
  return out;
}

}  // namespace autonav

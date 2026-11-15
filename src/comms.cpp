// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "comms.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <thread>

namespace autonav {

double comm_open_fraction(const DemStore& dem, const DemGrid& g, int row, int col,
                          const CommOptions& o) {
  double z0 = g.at(row, col);
  if (g.has_nodata && z0 == g.nodata) return -1;
  const double res = g.res_m();
  const double lat0 = g.lat_max - (row + 0.5) / g.ppd;
  const double coslat = std::max(0.05, std::cos(lat0 * kDeg));
  const double tmask = std::tan(o.mask_deg * kDeg);
  int open = 0;
  for (int a = 0; a < o.azimuths; ++a) {
    double az = 2 * kPi * a / o.azimuths;
    double se = std::sin(az), cn = std::cos(az);
    bool blocked = false;
    // Geometric steps: dense near the cell, sparse far away (masking needs height > d * tan(mask)).
    for (double d = res; d <= o.max_range_m && !blocked; d *= 1.3) {
      double dy_px = cn * d / res, dx_px = se * d / (res * coslat);
      int r = static_cast<int>(std::lround(row - dy_px)), c = static_cast<int>(std::lround(col + dx_px));
      double z;
      if (r >= 0 && r < g.height && (g.global || (c >= 0 && c < g.width))) {
        z = g.at(r, c);
        if (g.has_nodata && z == g.nodata) continue;
      } else {
        double lat = lat0 + cn * d / (kMarsRadiusM * kDeg);
        if (std::fabs(lat) > 89.9) break;
        double lon = g.lon_min + (col + 0.5) / g.ppd + se * d / (kMarsRadiusM * kDeg * coslat);
        z = dem.elev(lat, lon);
        if (z <= kNoData) continue;
      }
      double h = z - z0 - d * d / (2 * kMarsRadiusM);  // curvature drop
      if (h > d * tmask) blocked = true;
    }
    if (!blocked) ++open;
  }
  return static_cast<double>(open) / o.azimuths;
}

DemGrid compute_comm_coverage(const DemStore& dem, const DemGrid& g, const CommOptions& o) {
  DemGrid out;
  out.id = g.id + "_comms";
  out.name = "Relay comm coverage (terrain mask " + std::to_string(static_cast<int>(o.mask_deg)) +
             " deg) - " + g.name;
  out.source = "derived from " + g.id;
  out.citation = "Horizon masking of relay-orbiter passes over " + g.citation;
  out.ppd = g.ppd;
  out.lat_max = g.lat_max;
  out.lon_min = g.lon_min;
  out.width = g.width;
  out.height = g.height;
  out.global = g.global;
  out.has_nodata = true;
  out.nodata = 0;
  out.categorical = true;
  out.level = 1;
  auto cls = [](const char* name, const char* color, double loss) {
    ClassInfo c;
    c.name = name;
    c.color = color;
    c.group = "relay comms";
    c.comm = loss;
    return c;
  };
  out.classes = {{kCommOpen, cls("open sky", "none", 0.0)},
                 {kCommReduced, cls("reduced relay passes", "#ffd23f", 0.25)},
                 {kCommPoor, cls("poor comms", "#ff8c1a", 0.6)},
                 {kCommDead, cls("comm dead spot", "#e0182d", 1.0)}};
  out.data.assign(static_cast<size_t>(g.width) * g.height, 0);
  const int nthreads = std::max(1u, std::min(16u, std::thread::hardware_concurrency()));
  std::vector<std::thread> pool;
  for (int t = 0; t < nthreads; ++t)
    pool.emplace_back([&, t] {
      for (int y = t; y < g.height; y += nthreads)
        for (int x = 0; x < g.width; ++x) {
          double f = comm_open_fraction(dem, g, y, x, o);
          if (f < 0) continue;
          out.data[static_cast<size_t>(y) * g.width + x] =
              f >= 0.999 ? kCommOpen : f >= 0.75 ? kCommReduced : f >= 0.4 ? kCommPoor : kCommDead;
        }
    });
  for (auto& th : pool) th.join();
  return out;
}

DemStore load_or_build_comms(const DemStore& dem, const std::string& dir, bool rebuild, int* built,
                             std::string* err) {
  DemStore cached, out;
  if (!rebuild && std::filesystem::is_directory(dir)) cached.load_dir(dir, err);
  int n = 0;
  for (auto& g : dem.grids()) {
    if (g.categorical) continue;
    const DemGrid* hit = nullptr;
    for (auto& c : cached.grids())
      if (c.id == g.id + "_comms" && c.width == g.width && c.height == g.height) hit = &c;
    if (hit) {
      out.add(*hit);
      continue;
    }
    DemGrid cg = compute_comm_coverage(dem, g);
    std::string e;
    if (!save_grid(cg, dir, &e) && err) *err += cg.id + ": " + e + "\n";
    out.add(std::move(cg));
    ++n;
  }
  if (built) *built = n;
  return out;
}

}  // namespace autonav

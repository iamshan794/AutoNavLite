// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "terrain.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>

namespace autonav {
namespace {

// Box mean via an integral image over valid cells; columns wrap on global grids.
std::vector<float> box_mean(const DemGrid& g, const std::vector<float>& z, int r) {
  const int W = g.width, H = g.height, pad = g.global ? r : 0, WP = W + 2 * pad;
  const size_t stride = static_cast<size_t>(WP + 1);
  std::vector<double> S((H + 1) * stride, 0);
  std::vector<int32_t> C(S.size(), 0);
  for (int y = 0; y < H; ++y)
    for (int xp = 0; xp < WP; ++xp) {
      int x = ((xp - pad) % W + W) % W;
      float v = z[static_cast<size_t>(y) * W + x];
      bool ok = !std::isnan(v);
      size_t i = (y + 1) * stride + xp + 1;
      S[i] = (ok ? v : 0) + S[i - 1] + S[i - stride] - S[i - stride - 1];
      C[i] = (ok ? 1 : 0) + C[i - 1] + C[i - stride] - C[i - stride - 1];
    }
  std::vector<float> out(z.size(), NAN);
  for (int y = 0; y < H; ++y)
    for (int x = 0; x < W; ++x) {
      int y0 = std::max(0, y - r), y1 = std::min(H, y + r + 1);
      int x0 = std::max(0, x + pad - r), x1 = std::min(WP, x + pad + r + 1);
      auto box = [&](const auto& A) {
        return static_cast<double>(A[y1 * stride + x1]) - A[y0 * stride + x1] - A[y1 * stride + x0] +
               A[y0 * stride + x0];
      };
      double n = box(C);
      if (n > 0) out[static_cast<size_t>(y) * W + x] = static_cast<float>(box(S) / n);
    }
  return out;
}

double stddev(const std::vector<float>& v) {
  double s = 0, s2 = 0;
  size_t n = 0;
  for (float x : v)
    if (!std::isnan(x)) { s += x; s2 += static_cast<double>(x) * x; ++n; }
  if (n < 2) return 1;
  double m = s / n;
  return std::max(1e-6, std::sqrt(std::max(0.0, s2 / n - m * m)));
}

}  // namespace

DemGrid compute_landforms(const DemGrid& dem, double small_radius_m, double large_radius_m,
                          double plain_slope_deg) {
  DemGrid out;
  out.id = dem.id + "_landform";
  out.name = "Landforms (TPI, level 1) - " + dem.name;
  out.source = "derived from " + dem.id;
  out.citation = "Weiss 2001 / Jenness 2006 TPI landform classification over " + dem.citation;
  out.ppd = dem.ppd;
  out.lat_max = dem.lat_max;
  out.lon_min = dem.lon_min;
  out.width = dem.width;
  out.height = dem.height;
  out.global = dem.global;
  out.has_nodata = true;
  out.nodata = 0;
  out.categorical = true;
  out.level = 1;
  auto cls = [](const char* name, const char* color, const char* group) {
    ClassInfo c;
    c.name = name;
    c.color = color;
    c.group = group;
    return c;
  };
  out.classes = {{kValley, cls("valley / channel", "#3b6fd8", "low")},
                 {kBasin, cls("crater floor / basin", "#58b6c0", "low")},
                 {kPlain, cls("plain", "#9ccf6a", "flat")},
                 {kSlope, cls("slope / wall", "#e8c95a", "slope")},
                 {kUpland, cls("mesa / upland", "#c08a4a", "high")},
                 {kRidge, cls("ridge / crater rim", "#e0603a", "high")},
                 {kPeak, cls("peak / high ridge", "#b03060", "high")}};
  const int W = dem.width, H = dem.height;
  const size_t N = static_cast<size_t>(W) * H;
  out.data.assign(N, 0);
  std::vector<float> z(N);
  for (size_t i = 0; i < N; ++i)
    z[i] = dem.has_nodata && dem.data[i] == dem.nodata ? NAN : static_cast<float>(dem.data[i]);

  const double res = dem.res_m();
  const int rs = std::max(2, static_cast<int>(std::lround(small_radius_m / res)));
  const int rl = std::max(rs * 3, static_cast<int>(std::lround(large_radius_m / res)));
  auto ts = box_mean(dem, z, rs);  // becomes the TPI in place
  for (size_t i = 0; i < N; ++i) ts[i] = z[i] - ts[i];
  auto tl = box_mean(dem, z, rl);
  for (size_t i = 0; i < N; ++i) tl[i] = z[i] - tl[i];
  const double ss = stddev(ts), sl = stddev(tl);

  for (int y = 0; y < H; ++y) {
    double lat = dem.lat_max - (y + 0.5) / dem.ppd;
    double dx = res * std::max(0.01, std::cos(lat * kDeg)), dy = res;
    for (int x = 0; x < W; ++x) {
      size_t i = static_cast<size_t>(y) * W + x;
      if (std::isnan(z[i]) || std::isnan(ts[i]) || std::isnan(tl[i])) continue;
      auto zz = [&](int yy, int xx) {
        double v = dem.at(yy, xx);
        return dem.has_nodata && v == dem.nodata ? z[i] : v;
      };
      double gx = (zz(y, x + 1) - zz(y, x - 1)) / (2 * dx), gy = (zz(y - 1, x) - zz(y + 1, x)) / (2 * dy);
      double slope = std::atan(std::hypot(gx, gy)) / kDeg;
      double s = ts[i] / ss, l = tl[i] / sl;
      int16_t c;
      if (s <= -1) c = kValley;
      else if (s >= 1) c = l >= 1 ? kPeak : kRidge;
      else if (l <= -1) c = slope > plain_slope_deg ? kSlope : kBasin;
      else if (l >= 1) c = slope > plain_slope_deg ? kSlope : kUpland;
      else c = slope > plain_slope_deg ? kSlope : kPlain;
      out.data[i] = c;
    }
  }
  return out;
}

DemStore compute_landforms(const DemStore& dem) {
  DemStore out;
  for (auto& g : dem.grids())
    if (!g.categorical) out.add(compute_landforms(g));
  return out;
}

DemStore load_or_build_landforms(const DemStore& dem, const std::string& dir, bool rebuild,
                                 int* built, std::string* err) {
  DemStore cached, out;
  if (!rebuild && std::filesystem::is_directory(dir)) cached.load_dir(dir, err);
  int n = 0;
  for (auto& g : dem.grids()) {
    if (g.categorical) continue;
    const DemGrid* hit = nullptr;
    for (auto& c : cached.grids())
      if (c.id == g.id + "_landform" && c.width == g.width && c.height == g.height) hit = &c;
    if (hit) {
      out.add(*hit);
      continue;
    }
    DemGrid lf = compute_landforms(g);
    std::string e;
    if (!save_grid(lf, dir, &e) && err) *err += lf.id + ": " + e + "\n";
    out.add(std::move(lf));
    ++n;
  }
  if (built) *built = n;
  return out;
}

}  // namespace autonav

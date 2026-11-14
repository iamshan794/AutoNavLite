// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "render.hpp"

#include <algorithm>
#include <cmath>
#include <vector>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STBI_WRITE_NO_STDIO
#include "stb/stb_image_write.h"

namespace autonav {
namespace {
constexpr int T = 256;

void png_cb(void* ctx, void* data, int size) {
  static_cast<std::string*>(ctx)->append(static_cast<const char*>(data), size);
}

struct RGBA { unsigned char r, g, b, a; };

RGBA slope_color(double s, double maxs) {
  if (s < 5) return {0, 0, 0, 0};
  if (s >= maxs) return {200, 0, 0, 200};
  double t = (s - 5) / std::max(1.0, maxs - 5);  // 0 green -> 1 red
  auto c = [](double v) { return static_cast<unsigned char>(std::clamp(v, 0.0, 255.0)); };
  return {c(510 * t), c(510 * (1 - t)), 0, c(70 + 110 * t)};
}
}  // namespace

std::string render_tile(const DemStore& dem, int z, int row, int col, const RenderOptions& o) {
  if (z < 0 || z > 22 || row < 0 || col < 0 || row >= (1 << z) || col >= (2 << z)) return {};
  const double span = 180.0 / (1 << z);
  const double lat_top = 90.0 - row * span, lon_left = -180.0 + col * span;
  const double pdeg = span / T;
  const double m_per_deg = kMarsRadiusM * kDeg;

  // Sample elevation on a (T+2)^2 grid so gradients at tile edges are seamless.
  const int G = T + 2;
  std::vector<double> e(G * G);
  for (int y = 0; y < G; ++y) {
    double lat = std::clamp(lat_top - (y - 0.5) * pdeg, -89.999, 89.999);
    for (int x = 0; x < G; ++x) e[y * G + x] = dem.elev(lat, lon_left + (x - 0.5) * pdeg);
  }

  double interval = o.contour_interval_m;
  if (interval <= 0) {
    static const double steps[] = {2000, 1000, 500, 250, 100, 50, 25, 10};
    interval = steps[std::clamp(z - 2, 0, 7)];
  }
  const double az = o.sun_az_deg * kDeg, el = o.sun_el_deg * kDeg;
  const double sx = std::sin(az) * std::cos(el), sy = std::cos(az) * std::cos(el), sz = std::sin(el);

  std::vector<RGBA> img(T * T, RGBA{0, 0, 0, 0});
  for (int y = 0; y < T; ++y) {
    double lat = lat_top - (y + 0.5) * pdeg;
    double coslat = std::max(0.01, std::cos(lat * kDeg));
    double px_x = pdeg * m_per_deg * coslat, px_y = pdeg * m_per_deg;
    for (int x = 0; x < T; ++x) {
      double c = e[(y + 1) * G + x + 1];
      if (c <= kNoData) continue;
      double l = e[(y + 1) * G + x], r = e[(y + 1) * G + x + 2];
      double u = e[y * G + x + 1], d = e[(y + 2) * G + x + 1];
      if (l <= kNoData) l = c;
      if (r <= kNoData) r = c;
      if (u <= kNoData) u = c;
      if (d <= kNoData) d = c;
      double dzdx = (r - l) / (2 * px_x), dzdy = (u - d) / (2 * px_y);  // east, north
      RGBA& out = img[y * T + x];

      if (o.kind == "slope") {
        out = slope_color(std::atan(std::hypot(dzdx, dzdy)) / kDeg, o.max_slope_deg);
      } else if (o.kind == "contours") {
        double k = std::floor(c / interval);
        bool line = std::floor(r / interval) != k || std::floor(d / interval) != k;
        bool major = line && std::fmod(std::fabs(std::max({k, std::floor(r / interval),
                                                         std::floor(d / interval)})), 5.0) == 0;
        if (line) out = major ? RGBA{255, 240, 200, 230} : RGBA{255, 230, 170, 140};
      } else {  // hillshade (sun illumination) with optional cast shadows
        double nx = -dzdx, ny = -dzdy, nz = 1.0, nn = std::sqrt(nx * nx + ny * ny + 1.0);
        double shade = el <= 0 ? 0.0 : std::max(0.0, (nx * sx + ny * sy + nz * sz) / nn);
        if (shade > 0 && o.shadows && o.sun_el_deg < 60) {
          auto res = dem.elevation(lat, lon_left + (x + 0.5) * pdeg);
          double step = std::max(res.res_m, px_y);
          double rise = std::tan(el) * step;
          double la = lat, lo = lon_left + (x + 0.5) * pdeg, h = c;
          double dlat = std::cos(az) * step / m_per_deg;
          double dlon = std::sin(az) * step / (m_per_deg * coslat);
          for (int s = 0; s < 48; ++s) {
            la += dlat;
            lo += dlon;
            h += rise;
            if (h > 25000) break;  // above Olympus Mons
            double t = dem.elev(la, lo);
            if (t > h) { shade = 0; break; }
          }
        }
        double dark = 1.0 - shade;
        out = {0, 0, 20, static_cast<unsigned char>(std::clamp(dark * 210.0, 0.0, 210.0))};
      }
    }
  }
  std::string png;
  stbi_write_png_to_func(png_cb, &png, T, T, 4, img.data(), T * 4);
  return png;
}

std::string render_class_tile(const DemStore& classes, int z, int row, int col, int alpha) {
  if (z < 0 || z > 22 || row < 0 || col < 0 || row >= (1 << z) || col >= (2 << z)) return {};
  const double span = 180.0 / (1 << z), pdeg = span / T;
  const double lat_top = 90.0 - row * span, lon_left = -180.0 + col * span;
  std::vector<RGBA> img(T * T, RGBA{0, 0, 0, 0});
  for (int y = 0; y < T; ++y) {
    double lat = lat_top - (y + 0.5) * pdeg;
    for (int x = 0; x < T; ++x) {
      const DemGrid* g = nullptr;
      int c = classes.class_at(lat, lon_left + (x + 0.5) * pdeg, &g);
      if (!c || !g) continue;
      auto it = g->classes.find(c);
      if (it == g->classes.end() || it->second.color == "none") continue;
      unsigned v = std::stoul(it->second.color.substr(1), nullptr, 16);
      const ClassInfo& ci = it->second;
      // Uncertain cells faint; benign classes (low planner cost) half-transparent so hazards stand out.
      int a = alpha;
      if (ci.name == "uncertain") a = alpha / 3;
      else if (ci.rock >= 0 && ci.rock + std::max(0.0, ci.soft) <= 0.3) a = alpha / 2;
      img[y * T + x] = {static_cast<unsigned char>(v >> 16), static_cast<unsigned char>(v >> 8),
                        static_cast<unsigned char>(v), static_cast<unsigned char>(a)};
    }
  }
  std::string png;
  stbi_write_png_to_func(png_cb, &png, T, T, 4, img.data(), T * 4);
  return png;
}

}  // namespace autonav

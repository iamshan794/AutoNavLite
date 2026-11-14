// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "dem.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>

#include "nlohmann/json.hpp"

namespace autonav {
namespace fs = std::filesystem;

double wrap360(double lon) {
  double r = std::fmod(lon, 360.0);
  return r < 0 ? r + 360.0 : r;
}
double wrap180(double lon) {
  double r = wrap360(lon);
  return r > 180.0 ? r - 360.0 : r;
}

double haversine_m(double lat1, double lon1, double lat2, double lon2) {
  double p1 = lat1 * kDeg, p2 = lat2 * kDeg;
  double dp = p2 - p1, dl = (lon2 - lon1) * kDeg;
  double a = std::sin(dp / 2) * std::sin(dp / 2) +
             std::cos(p1) * std::cos(p2) * std::sin(dl / 2) * std::sin(dl / 2);
  return 2 * kMarsRadiusM * std::asin(std::min(1.0, std::sqrt(a)));
}

bool DemGrid::contains(double lat, double lon360) const {
  if (global) return lat <= lat_max && lat >= lat_max - height / ppd;
  double dx = wrap360(lon360 - lon_min);
  return lat <= lat_max && lat >= lat_max - height / ppd && dx <= width / ppd;
}

double DemGrid::at(int row, int col) const {
  row = std::clamp(row, 0, height - 1);
  if (global) {
    col %= width;
    if (col < 0) col += width;
  } else {
    col = std::clamp(col, 0, width - 1);
  }
  return data[static_cast<size_t>(row) * width + col];
}

double DemGrid::sample(double lat, double lon360) const {
  if (!contains(lat, lon360)) return kNoData;
  if (categorical) {
    int y = static_cast<int>(std::floor((lat_max - lat) * ppd));
    int x = static_cast<int>(std::floor(wrap360(lon360 - lon_min) * ppd));
    double v = at(y, x);
    return has_nodata && v == nodata ? kNoData : v;
  }
  double fy = (lat_max - lat) * ppd - 0.5;
  double fx = wrap360(lon360 - lon_min) * ppd - 0.5;
  int y0 = static_cast<int>(std::floor(fy)), x0 = static_cast<int>(std::floor(fx));
  double ty = fy - y0, tx = fx - x0;
  double a = at(y0, x0), b = at(y0, x0 + 1), c = at(y0 + 1, x0), d = at(y0 + 1, x0 + 1);
  if (has_nodata && (a == nodata || b == nodata || c == nodata || d == nodata)) return kNoData;
  return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
}

int DemStore::load_dir(const std::string& dir, std::string* err) {
  int n = 0;
  if (!fs::is_directory(dir)) {
    if (err) *err = "DEM dir not found: " + dir;
    return 0;
  }
  for (auto& e : fs::directory_iterator(dir)) {
    if (e.path().extension() != ".json") continue;
    try {
      std::ifstream jf(e.path());
      auto j = nlohmann::json::parse(jf);
      DemGrid g;
      g.id = e.path().stem().string();
      g.name = j.value("name", g.id);
      g.source = j.value("source", "");
      g.citation = j.value("citation", "");
      g.ppd = j.at("ppd");
      g.lat_max = j.at("lat_max");
      g.lon_min = wrap360(j.at("lon_min").get<double>());
      g.width = j.at("width");
      g.height = j.at("height");
      g.global = j.value("global", false);
      if (j.contains("nodata")) {
        g.has_nodata = true;
        g.nodata = static_cast<int16_t>(j["nodata"].get<int>());
      }
      g.categorical = j.value("categorical", false);
      g.level = j.value("level", 0);
      if (j.contains("classes"))
        for (auto& [k, v] : j["classes"].items()) {
          ClassInfo c;
          c.name = v.get<std::string>();
          auto str = [&](const char* key, std::string& out) {
            if (j.contains(key) && j[key].contains(k)) out = j[key][k].get<std::string>();
          };
          str("colors", c.color);
          str("groups", c.group);
          str("codes", c.code);
          if (j.contains("planner") && j["planner"].contains(k)) {
            c.rock = j["planner"][k].value("rock", -1.0);
            c.soft = j["planner"][k].value("soft", -1.0);
            c.comm = j["planner"][k].value("comm", -1.0);
          }
          g.classes[std::stoi(k)] = c;
        }
      if (j.value("type", "int16be") != "int16be") throw std::runtime_error("unsupported type");
      auto img = e.path().parent_path() / j.at("file").get<std::string>();
      std::ifstream f(img, std::ios::binary);
      size_t count = static_cast<size_t>(g.width) * g.height;
      std::vector<unsigned char> raw(count * 2);
      if (!f.read(reinterpret_cast<char*>(raw.data()), raw.size()))
        throw std::runtime_error("short read " + img.string());
      g.data.resize(count);
      for (size_t i = 0; i < count; ++i)
        g.data[i] = static_cast<int16_t>((raw[2 * i] << 8) | raw[2 * i + 1]);
      add(std::move(g));
      ++n;
    } catch (const std::exception& ex) {
      if (err) *err += e.path().string() + ": " + ex.what() + "\n";
    }
  }
  return n;
}

bool save_grid(const DemGrid& g, const std::string& dir, std::string* err) {
  try {
    fs::create_directories(dir);
    nlohmann::json j = {{"name", g.name}, {"source", g.source}, {"citation", g.citation},
                        {"file", g.id + ".img"}, {"ppd", g.ppd}, {"lat_max", g.lat_max},
                        {"lon_min", g.lon_min}, {"width", g.width}, {"height", g.height},
                        {"type", "int16be"}, {"global", g.global}, {"categorical", g.categorical},
                        {"level", g.level}};
    if (g.has_nodata) j["nodata"] = g.nodata;
    for (auto& [k, c] : g.classes) {
      auto key = std::to_string(k);
      j["classes"][key] = c.name;
      j["colors"][key] = c.color;
      if (!c.group.empty()) j["groups"][key] = c.group;
      if (c.rock >= 0 || c.soft >= 0 || c.comm >= 0)
        j["planner"][key] = {{"rock", c.rock}, {"soft", c.soft}, {"comm", c.comm}};
    }
    std::vector<unsigned char> raw(g.data.size() * 2);
    for (size_t i = 0; i < g.data.size(); ++i) {
      raw[2 * i] = static_cast<unsigned char>(static_cast<uint16_t>(g.data[i]) >> 8);
      raw[2 * i + 1] = static_cast<unsigned char>(g.data[i] & 0xff);
    }
    std::ofstream(fs::path(dir) / (g.id + ".img"), std::ios::binary)
        .write(reinterpret_cast<const char*>(raw.data()), raw.size());
    std::ofstream(fs::path(dir) / (g.id + ".json")) << j.dump(1);
    return true;
  } catch (const std::exception& ex) {
    if (err) *err = ex.what();
    return false;
  }
}

void DemStore::add(DemGrid g) {
  grids_.push_back(std::move(g));
  std::stable_sort(grids_.begin(), grids_.end(),
                   [](const DemGrid& a, const DemGrid& b) { return a.ppd > b.ppd; });
}

ElevSample DemStore::elevation(double lat, double lon) const {
  double l = wrap360(lon);
  for (auto& g : grids_) {
    double v = g.sample(lat, l);
    if (v > kNoData) return {v, g.res_m(), &g};
  }
  return {};
}

int DemStore::class_at(double lat, double lon, const DemGrid** grid) const {
  double l = wrap360(lon);
  for (auto& g : grids_) {
    double v = g.sample(lat, l);
    if (v > kNoData && v != 0) {
      if (grid) *grid = &g;
      return static_cast<int>(v);
    }
  }
  return 0;
}

double DemStore::best_res_m(double lat, double lon) const {
  double l = wrap360(lon);
  for (auto& g : grids_)
    if (g.contains(lat, l)) return g.res_m();
  return 0;
}

}  // namespace autonav

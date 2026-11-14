// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — DEM store (MOLA MEGDR raw int16 grids, global + regional patches)
#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace autonav {

constexpr double kMarsRadiusM = 3396190.0;  // IAU Mars 2000 sphere
constexpr double kPi = 3.14159265358979323846;
constexpr double kDeg = kPi / 180.0;
constexpr double kNoData = -1e30;

// One class of a categorical grid: display name/colour, hierarchy group and the planner effect
// (rock = wheel damage / roughness, soft = sinkage risk, comm = relay-comms loss; 0..1, negative =
// no effect). color "none" = not drawn.
struct ClassInfo {
  std::string name, color = "#808080", group, code;
  double rock = -1, soft = -1, comm = -1;
};

// Equirectangular grid of elevations, pixel-centred; longitudes stored 0..360 east.
struct DemGrid {
  std::string name, source, citation, id;
  double ppd = 0;       // pixels per degree
  double lat_max = 0;   // north edge
  double lon_min = 0;   // west edge (0..360 east)
  int width = 0, height = 0;
  bool global = false;
  bool has_nodata = false;
  int16_t nodata = 0;
  // Categorical grids (terrain classes) use nearest-neighbour sampling.
  bool categorical = false;
  int level = 0;
  std::map<int, ClassInfo> classes;
  std::vector<int16_t> data;

  double res_m() const { return kMarsRadiusM * kDeg / ppd; }
  bool contains(double lat, double lon360) const;
  // Bilinear sample; returns kNoData outside the grid or next to no-data cells.
  double sample(double lat, double lon360) const;
  double at(int row, int col) const;
};

struct ElevSample {
  double elev = kNoData;
  double res_m = 0;
  const DemGrid* grid = nullptr;
};

class DemStore {
 public:
  // Loads every <name>.json + <name>.img pair in dir. Returns number of grids loaded.
  int load_dir(const std::string& dir, std::string* err = nullptr);
  void add(DemGrid g);  // for tests / synthetic data

  // Highest-resolution elevation at (lat, lon east, any range).
  ElevSample elevation(double lat, double lon) const;
  double elev(double lat, double lon) const { return elevation(lat, lon).elev; }
  // Finest grid resolution available at a point (metres / pixel).
  double best_res_m(double lat, double lon) const;
  // Categorical lookup: class code (0 = none) from the finest grid covering the point.
  int class_at(double lat, double lon, const DemGrid** grid = nullptr) const;
  const std::vector<DemGrid>& grids() const { return grids_; }

 private:
  std::vector<DemGrid> grids_;  // sorted finest first
};

// Writes <dir>/<g.id>.json + .img in the format load_dir() reads.
bool save_grid(const DemGrid& g, const std::string& dir, std::string* err = nullptr);

double wrap360(double lon);
double wrap180(double lon);
// Great-circle distance in metres between two lat/lon points (degrees).
double haversine_m(double lat1, double lon1, double lat2, double lon2);

}  // namespace autonav

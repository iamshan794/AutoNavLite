// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// Minimal self-contained tests (no framework dependency).
#include <cmath>
#include <cstdio>

#include "comms.hpp"
#include "dem.hpp"
#include "mars_time.hpp"
#include "planner.hpp"
#include "terrain.hpp"

using namespace autonav;

static int failures = 0;
#define CHECK_NEAR(a, b, tol)                                                          \
  do {                                                                                 \
    double _a = (a), _b = (b);                                                         \
    if (std::fabs(_a - _b) > (tol)) {                                                  \
      std::printf("FAIL %s:%d %s = %.6f, expected %.6f\n", __FILE__, __LINE__, #a, _a, \
                  _b);                                                                 \
      ++failures;                                                                      \
    }                                                                                  \
  } while (0)
#define CHECK(c)                                              \
  do {                                                        \
    if (!(c)) {                                               \
      std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); \
      ++failures;                                             \
    }                                                         \
  } while (0)

// Worked examples from the NASA GISS Mars24 algorithm page.
static void test_mars24() {
  auto t = mars_time_from_unix(947116800.0);  // 2000-01-06 00:00:00 UTC
  CHECK_NEAR(t.jd_tt, 2451549.50074, 1e-5);
  CHECK_NEAR(t.ls_deg, 277.18758, 1e-3);
  CHECK_NEAR(t.eot_deg, -5.18774, 1e-3);
  CHECK_NEAR(t.mst_h, 23.99425, 1e-4);
  CHECK_NEAR(t.subsolar_lon_w, 174.72600, 1e-3);
  CHECK_NEAR(t.declination_deg, -25.22825, 1e-3);
  CHECK_NEAR(t.helio_dist_au, 1.39358, 1e-4);
  auto s = sun_position(t, 0, 0);
  CHECK_NEAR(s.ltst_h, 23.64840, 1e-4);
  CHECK_NEAR(90 - s.elevation_deg, 154.26182, 1e-3);
  CHECK_NEAR(s.azimuth_deg, 191.03905, 1e-3);

  // Spirit landing target: local true midnight at 2004-01-03 13:46:31 UTC, azimuth 180.
  auto t2 = mars_time_from_unix(1073137591.0);
  auto s2 = sun_position(t2, -14.640, -184.702);
  double lt = s2.ltst_h > 12 ? s2.ltst_h - 24 : s2.ltst_h;
  CHECK_NEAR(lt, 0.0, 2e-3);
  CHECK_NEAR(s2.azimuth_deg, 180.0, 0.5);
}

static DemGrid synthetic(double ppd, int n, double (*f)(double, double)) {
  DemGrid g;
  g.id = "synthetic";
  g.ppd = ppd;
  g.lat_max = 1.0;
  g.lon_min = 0.0;
  g.width = g.height = n;
  g.data.resize(n * n);
  for (int y = 0; y < n; ++y)
    for (int x = 0; x < n; ++x)
      g.data[y * n + x] = static_cast<int16_t>(f(1.0 - (y + 0.5) / ppd, (x + 0.5) / ppd));
  return g;
}

static void test_dem() {
  DemStore d;
  d.add(synthetic(128, 256, [](double lat, double lon) { return 1000 * lat + 10 * lon; }));
  CHECK_NEAR(d.elev(0.5, 0.5), 505, 1.0);
  CHECK_NEAR(d.elev(0.5, -359.5), 505, 1.0);  // longitude wrap
  CHECK(d.elev(5.0, 0.5) <= kNoData);         // outside coverage
}

// A 600 m mesa (~40 deg walls) blocks the straight line between start and goal, with a
// gap to the north. The safe profile (25 deg limit) must detour through the gap; the
// shortest profile (45 deg physical limit) climbs straight over it.
static double mesa(double lat, double lon) {
  return (std::fabs(lon - 1.0) < 0.06 && lat < 0.42) ? 600.0 : 0.0;
}

static void test_planner() {
  DemStore d;
  d.add(synthetic(128, 256, mesa));
  RouteRequest rq{};
  rq.lat0 = 0.3; rq.lon0 = 0.6; rq.lat1 = 0.3; rq.lon1 = 1.4;
  rq.max_grid = 200;

  rq.profile = RouteProfile::Safe;
  auto safe = plan_route(d, rq);
  CHECK(safe.ok);
  double max_lat = -90;
  for (auto& p : safe.points) max_lat = std::fmax(max_lat, p.lat);
  CHECK(max_lat > 0.42);
  CHECK(safe.max_slope_deg <= rq.max_slope_deg);
  CHECK(safe.dist_m > 0 && safe.rover_sols > 0 && safe.eva_hours > 0);
  CHECK(!safe.reloc_stops.empty());  // 2% drift, 50 m threshold => stop every ~2.5 km

  rq.profile = RouteProfile::Shortest;
  auto shortest = plan_route(d, rq);
  CHECK(shortest.ok);
  CHECK(shortest.dist_m < safe.dist_m);
  CHECK(shortest.max_slope_deg > rq.max_slope_deg);

  // A reference trajectory straight over the mesa is scored with the same model, not re-planned.
  rq.profile = RouteProfile::Safe;
  rq.reference = {{0.3, 0.6}, {0.3, 1.0}, {0.3, 1.4}};
  auto v = plan_compare(d, rq, {RouteProfile::Safe}, 0);
  CHECK(v.size() == 2);
  if (v.size() == 2) {
    CHECK(v[1].ok && v[1].reference && !v[0].reference);
    CHECK(v[1].max_slope_deg > rq.max_slope_deg);  // historic path crossed what Safe avoids
    CHECK(std::fabs(v[1].dist_m - shortest.dist_m) < 0.1 * shortest.dist_m);
  }
  rq.reference.clear();
}

// Gentle (<10 deg) but bumpy band blocking the straight line: the safe profile drives through,
// the wheel-saver detours through the smooth gap and ends with a lower wear index.
static double bumpy_band(double lat, double lon) {
  if (std::fabs(lon - 1.0) < 0.06 && lat < 0.42) {
    int px = static_cast<int>(lon * 128), py = static_cast<int>(lat * 128);
    return ((px + py) % 2) ? 25.0 : -25.0;
  }
  return 0.0;
}

static void test_wear_and_compare() {
  DemStore d;
  d.add(synthetic(128, 256, bumpy_band));
  RouteRequest rq{};
  rq.lat0 = 0.3; rq.lon0 = 0.6; rq.lat1 = 0.3; rq.lon1 = 1.4;
  rq.max_grid = 200;
  auto v = plan_compare(d, rq, {RouteProfile::Wear, RouteProfile::Shortest}, 1);
  CHECK(v.size() == 3);  // two profiles + one alternative
  if (v.size() < 3) return;
  const auto &wear = v[0], &shortest = v[1], &alt = v[2];
  CHECK(wear.ok && shortest.ok && alt.ok);
  CHECK(wear.wear_index < shortest.wear_index);
  CHECK(wear.dist_m > shortest.dist_m);
  CHECK(wear.rough_km < shortest.rough_km);
  CHECK(alt.alternative && alt.overlap < 0.7);

  // Rocky (high thermal inertia) strip on flat ground, open to the south: avoided by the wheel saver.
  DemStore flat;
  flat.add(synthetic(128, 256, [](double, double) { return 0.0; }));
  DemStore ti;
  ti.add(synthetic(8, 16, [](double lat, double lon) {
    return (std::fabs(lon - 1.0) < 0.15 && lat > 0.2) ? 600.0 : 200.0;
  }));
  rq.profile = RouteProfile::Wear;
  auto rocky = plan_route(flat, rq, &ti);
  CHECK(rocky.ok && rocky.ti_available);
  double min_lat = 90;
  for (auto& p : rocky.points) min_lat = std::fmin(min_lat, p.lat);
  CHECK(min_lat < 0.25);  // south of the rocky part (TI < 300 there)
  CHECK(rocky.rock_km < 1.0);
}

static double crater(double lat, double lon) {
  double r = std::hypot(lat, lon - 1.0);
  if (r < 0.06) return -500;
  if (r < 0.1) return -500 + 700 * (r - 0.06) / 0.04;  // wall up to the rim
  if (r < 0.15) return 200 * (0.15 - r) / 0.05;        // ejecta apron
  return 0;
}

static void test_terrain_classes() {
  // Categorical grids sample nearest-neighbour: never a blended class code.
  DemGrid m = synthetic(128, 256, [](double, double lon) { return lon < 1.0 ? 1.0 : 3.0; });
  m.categorical = true;
  m.has_nodata = true;
  m.nodata = 0;
  DemStore mat;
  mat.add(m);
  for (double lon = 0.98; lon < 1.02; lon += 0.001) {
    int c = mat.class_at(0.0, lon);
    CHECK(c == 1 || c == 3);
  }
  CHECK(mat.class_at(5.0, 1.0) == 0);  // outside coverage

  // Level 1 landforms: crater floor reads as basin, the rim as ridge.
  auto lf = compute_landforms(synthetic(128, 256, crater), 1500, 15000);
  CHECK(lf.categorical && lf.level == 1);
  DemStore L;
  L.add(lf);
  CHECK(L.class_at(0.0, 1.0) == kBasin);
  int rim = L.class_at(0.1, 1.0);
  CHECK(rim == kRidge || rim == kPeak);

  // Run-once relief cache: the second call loads the saved grid instead of recomputing it.
  DemStore crater_dem;
  crater_dem.add(synthetic(128, 256, crater));
  const std::string dir = "/tmp/autonav_test_relief";
  int built = -1;
  auto first = load_or_build_landforms(crater_dem, dir, true, &built);
  CHECK(built == 1);
  auto again = load_or_build_landforms(crater_dem, dir, false, &built);
  CHECK(built == 0 && again.grids().size() == 1);
  if (again.grids().size() == 1) {
    CHECK(again.grids()[0].data == first.grids()[0].data);
    CHECK(again.grids()[0].categorical && again.grids()[0].classes.at(kBasin).group == "low");
  }

  // Terrain classes: a mapped dune strip (soft = 1 in the class table) (open to the south) is avoided by the Safe profile.
  DemStore flat;
  flat.add(synthetic(128, 256, [](double, double) { return 0.0; }));
  DemGrid sand = synthetic(128, 256, [](double lat, double lon) {
    return (std::fabs(lon - 1.0) < 0.15 && lat > 0.2) ? 1.0 : 4.0;
  });
  sand.categorical = true;
  sand.classes[1].name = "aeolian straight";
  sand.classes[1].soft = 1.0;
  sand.classes[4].name = "smooth terrain";
  sand.classes[4].rock = sand.classes[4].soft = 0.0;
  DemStore sm;
  sm.add(sand);
  RouteRequest rq{};
  rq.lat0 = 0.3; rq.lon0 = 0.6; rq.lat1 = 0.3; rq.lon1 = 1.4;
  rq.max_grid = 200;
  rq.profile = RouteProfile::Safe;
  auto blind = plan_route(flat, rq);
  auto aware = plan_route(flat, rq, nullptr, &sm);
  CHECK(aware.ok && aware.material_available && !blind.material_available);
  CHECK(aware.soft_km < 1.0);
  CHECK(aware.dist_m > blind.dist_m);
}

static void test_comms() {
  // A 3 km-deep, ~11 km-wide E-W canyon: walls mask almost the whole sky on the floor.
  DemStore canyon;
  canyon.add(synthetic(128, 256, [](double lat, double) { return std::fabs(lat - 0.5) < 0.1 ? -3000.0 : 0.0; }));
  auto cg = compute_comm_coverage(canyon, canyon.grids()[0]);
  DemStore C;
  C.add(cg);
  CHECK(C.class_at(0.5, 1.0) == kCommDead);
  CHECK(C.class_at(0.9, 1.0) == kCommOpen);
  CHECK(cg.classes.at(kCommDead).comm == 1.0);

  // Planner: a comm dead strip (open to the south) is avoided by Safe unless comm_weight = 0.
  DemStore flat;
  flat.add(synthetic(128, 256, [](double, double) { return 0.0; }));
  DemGrid dead = synthetic(128, 256, [](double lat, double lon) {
    return (std::fabs(lon - 1.0) < 0.15 && lat > 0.2) ? 4.0 : 1.0;
  });
  dead.categorical = true;
  dead.classes = cg.classes;
  DemStore D;
  D.add(dead);
  RouteRequest rq{};
  rq.lat0 = 0.3; rq.lon0 = 0.6; rq.lat1 = 0.3; rq.lon1 = 1.4;
  rq.max_grid = 200;
  rq.profile = RouteProfile::Safe;
  rq.comm_weight = 4.0;
  auto aware = plan_route(flat, rq, nullptr, nullptr, &D);
  rq.comm_weight = 0.0;
  auto blind = plan_route(flat, rq, nullptr, nullptr, &D);
  CHECK(aware.ok && aware.comm_available);
  CHECK(aware.comm_dead_km < 1.0);
  CHECK(blind.comm_dead_km > 10.0);
}

int main() {
  test_mars24();
  test_comms();
  test_terrain_classes();
  test_dem();
  test_planner();
  test_wear_and_compare();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("all tests passed\n");
  return 0;
}

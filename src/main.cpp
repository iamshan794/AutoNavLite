// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// autonav0d — AutoNav-0 headless Mars cartography & route-planning daemon.
//
// Serves (localhost TCP or Unix domain socket):
//   /                       2D web UI (web/)
//   /api/*                  JSON API (layers, bookmarks, nomenclature, elevation, profile,
//                           route, sun, vectors, status)
//   /tiles/<layer>/z/r/c    NASA Trek WMTS tiles from offline cache (+ upstream fill if online)
//   /dem/<kind>/z/r/c.png   DEM-derived rasters (hillshade+shadows, slope, contours)
#include <sys/socket.h>
#include <unistd.h>

#include <array>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <deque>
#include <mutex>
#include <sstream>

#include "dem.hpp"
#include "httplib/httplib.h"
#include "mars_time.hpp"
#include "nlohmann/json.hpp"
#include "planner.hpp"
#include "render.hpp"
#include "comms.hpp"
#include "terrain.hpp"
#include "tile_cache.hpp"

using json = nlohmann::json;
using namespace autonav;
namespace fs = std::filesystem;

namespace {

constexpr const char* kVersion = "0.1.0";
httplib::Server* g_server = nullptr;

struct Config {
  std::string host = "127.0.0.1";
  int port = 8080;
  std::string unix_socket;
  std::string data_dir = "data";
  std::string web_dir = "web";
  std::string upstream = "https://trek.nasa.gov/tiles/Mars/EQ";
  bool offline = false;
  bool build_relief = false;
  std::string rerun_url;  // advertised to the UI (Rerun web viewer for point clouds / VSLAM)
};

void log(const std::string& msg) {
  auto t = std::time(nullptr);
  char buf[32];
  std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%S", std::gmtime(&t));
  std::cerr << buf << "Z autonav0d: " << msg << std::endl;
}

void usage() {
  std::cout << "autonav0d " << kVersion << " - AutoNav-0 headless Mars map & route service\n\n"
            << "  --host ADDR      bind address (default 127.0.0.1)\n"
            << "  --port N         TCP port (default 8080)\n"
            << "  --unix PATH      listen on a Unix domain socket instead of TCP\n"
            << "  --data DIR       data directory (default ./data)\n"
            << "  --web DIR        static UI directory (default ./web, '' to disable)\n"
            << "  --upstream URL   WMTS upstream (default NASA Trek Mars EQ)\n"
            << "  --offline        never contact the network (cache only)\n"
            << "  --rerun-url URL  Rerun web viewer URL to advertise in the UI (optional)\n"
            << "  --build-relief   recompute the DEM-derived grids (landforms in data/terrain/relief,\n"
            << "                   comm coverage in data/terrain/comms) and exit\n";
}

std::string read_file(const fs::path& p) {
  std::ifstream f(p, std::ios::binary);
  if (!f) return {};
  return std::string(std::istreambuf_iterator<char>(f), {});
}

void send_json(httplib::Response& res, const json& j, int status = 200) {
  res.status = status;
  res.set_content(j.dump(), "application/json");
}

void send_error(httplib::Response& res, int status, const std::string& msg) {
  send_json(res, {{"error", msg}}, status);
}

double qnum(const httplib::Request& req, const char* key, double def) {
  if (!req.has_param(key)) return def;
  try {
    return std::stod(req.get_param_value(key));
  } catch (...) {
    throw std::invalid_argument(std::string("bad numeric parameter: ") + key);
  }
}

bool parse_latlon(const std::string& s, double& lat, double& lon) {
  char comma;
  std::istringstream is(s);
  return (is >> lat >> comma >> lon) && comma == ',' && lat >= -90 && lat <= 90;
}

double hard_limit_for(RouteProfile p, double max_slope) {
  return p == RouteProfile::Shortest ? 45.0 : max_slope;
}

double r1(double v) { return std::round(v * 10) / 10; }
double r2(double v) { return std::round(v * 100) / 100; }

json route_to_geojson(const RouteResult& r, const RouteRequest& rq) {
  json coords = json::array(), samples = json::array(), stops = json::array();
  for (auto& p : r.points) {
    coords.push_back({p.lon, p.lat, r1(p.elev)});
    samples.push_back({{"d", std::round(p.dist_m)}, {"elev", std::round(p.elev)},
                       {"slope", r1(p.slope_deg)}, {"sigma", r1(p.sigma_m)}, {"wear", r2(p.wear)}});
  }
  for (int i : r.reloc_stops) {
    auto& p = r.points[i];
    stops.push_back({{"lat", p.lat}, {"lon", p.lon}, {"dist_m", std::round(p.dist_m)}});
  }
  return {
      {"type", "Feature"},
      {"id", r.id},
      {"geometry", {{"type", "LineString"}, {"coordinates", coords}}},
      {"properties",
       {{"id", r.id},
        {"label", r.label},
        {"profile", profile_name(r.profile)},
        {"alternative", r.alternative},
        {"reference", r.reference},
        {"overlap_with_primary", r2(r.overlap)},
        {"max_slope_limit_deg", hard_limit_for(r.profile, rq.max_slope_deg)},
        {"distance_m", std::round(r.dist_m)},
        {"distance_3d_m", std::round(r.dist3d_m)},
        {"ascent_m", std::round(r.ascent_m)},
        {"descent_m", std::round(r.descent_m)},
        {"max_slope_deg", r1(r.max_slope_deg)},
        {"mean_slope_deg", r1(r.mean_slope_deg)},
        {"eva_hours", r2(r.eva_hours)},
        {"rover_sols", r1(r.rover_sols)},
        {"rover_energy_wh", std::round(r.rover_energy_wh)},
        {"wear_index", r2(r.wear_index)},
        {"turn_deg", std::round(r.turn_deg)},
        {"rough_km", r2(r.rough_km)},
        {"rock_km", r2(r.rock_km)},
        {"soft_km", r2(r.soft_km)},
        {"thermal_inertia", r.ti_available},
        {"terrain_classes", r.material_available},
        {"comm_coverage", r.comm_available},
        {"comm_dead_km", r2(r.comm_dead_km)},
        {"comm_poor_km", r2(r.comm_poor_km)},
        {"dem_resolution_m", std::round(r.dem_res_m)},
        {"grid_cell_m", std::round(r.cell_m)},
        {"grid", {r.grid_w, r.grid_h}},
        {"nodes_expanded", r.expanded},
        {"reloc_stops", stops},
        {"samples", samples}}}};
}

// Parses common route parameters; returns an error message or empty string.
std::string parse_route_request(const httplib::Request& req, RouteRequest& rq) {
  if (!parse_latlon(req.get_param_value("from"), rq.lat0, rq.lon0) ||
      !parse_latlon(req.get_param_value("to"), rq.lat1, rq.lon1))
    return "from/to must be 'lat,lon'";
  rq.profile = parse_profile(req.get_param_value("profile"));
  rq.max_slope_deg = std::clamp(qnum(req, "max_slope", 25), 2.0, 60.0);
  rq.max_grid = std::clamp(static_cast<int>(qnum(req, "grid", 400)), 32, 1000);
  rq.rover_m_per_sol = std::max(1.0, qnum(req, "m_per_sol", 150));
  rq.odo_drift_frac = std::clamp(qnum(req, "drift", 0.02), 0.0, 0.5);
  rq.comm_weight = std::clamp(qnum(req, "comm_weight", 1.0), 0.0, 10.0);
  rq.reloc_sigma_m = std::max(1.0, qnum(req, "reloc_sigma", 50));
  if (haversine_m(rq.lat0, rq.lon0, rq.lat1, rq.lon1) > 2.0e6)
    return "route longer than 2000 km; add waypoints";
  return {};
}

}  // namespace

int main(int argc, char** argv) {
  Config cfg;
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&]() -> std::string {
      if (i + 1 >= argc) {
        std::cerr << "missing value for " << a << "\n";
        std::exit(2);
      }
      return argv[++i];
    };
    if (a == "--host") cfg.host = next();
    else if (a == "--port") cfg.port = std::stoi(next());
    else if (a == "--unix") cfg.unix_socket = next();
    else if (a == "--data") cfg.data_dir = next();
    else if (a == "--web") cfg.web_dir = next();
    else if (a == "--upstream") cfg.upstream = next();
    else if (a == "--offline") cfg.offline = true;
    else if (a == "--rerun-url") cfg.rerun_url = next();
    else if (a == "--build-relief") cfg.build_relief = true;
    else if (a == "-h" || a == "--help") { usage(); return 0; }
    else if (a == "--version") { std::cout << kVersion << "\n"; return 0; }
    else { std::cerr << "unknown option " << a << "\n"; usage(); return 2; }
  }

  const fs::path data(cfg.data_dir);
  DemStore dem;
  std::string err;
  int ndem = dem.load_dir((data / "dem").string(), &err);
  if (!err.empty()) log("DEM warnings: " + err);
  log("loaded " + std::to_string(ndem) + " DEM grid(s)");
  if (ndem == 0) log("WARNING: no DEM loaded - run scripts/fetch_data.py --dem");

  DemStore ti;
  std::string ti_err;
  int nti = ti.load_dir((data / "ti").string(), &ti_err);
  if (!ti_err.empty()) log("TI warnings: " + ti_err);
  log(nti ? "loaded thermal inertia grid" : "no thermal inertia grid (wear model uses DEM only)");
  const DemStore* ti_ptr = nti ? &ti : nullptr;

  // Terrain classes. Relief: TPI landforms from the DEM, computed once and saved under
  // data/terrain/relief. Terrain: DoMars16k landform maps precomputed from CTX imagery by
  // scripts/terrain_map.py (data/terrain/terrain_*.json).
  int nbuilt = 0;
  std::string rel_err;
  DemStore landform = load_or_build_landforms(dem, (data / "terrain" / "relief").string(),
                                              cfg.build_relief, &nbuilt, &rel_err);
  if (!rel_err.empty()) log("relief warnings: " + rel_err);
  // Relay comm coverage (terrain masking of orbiter passes), also computed once and cached.
  int ncomm_built = 0;
  std::string comm_err;
  auto tc = std::chrono::steady_clock::now();
  DemStore comms = load_or_build_comms(dem, (data / "terrain" / "comms").string(), cfg.build_relief,
                                       &ncomm_built, &comm_err);
  if (!comm_err.empty()) log("comms warnings: " + comm_err);
  if (ncomm_built)
    log("built " + std::to_string(ncomm_built) + " comm-coverage grid(s) in " +
        std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - tc).count()) + " ms");
  if (cfg.build_relief) {
    log("built " + std::to_string(nbuilt) + " relief grid(s) in " + (data / "terrain" / "relief").string());
    return rel_err.empty() && comm_err.empty() ? 0 : 1;
  }
  const DemStore* comm_ptr = comms.grids().empty() ? nullptr : &comms;
  DemStore terrain;
  std::string ter_err;
  int nter = terrain.load_dir((data / "terrain").string(), &ter_err);
  if (!ter_err.empty()) log("terrain warnings: " + ter_err);
  log("relief grids: " + std::to_string(landform.grids().size()) + " (" + std::to_string(nbuilt) +
      " built now), terrain-class grids: " + std::to_string(nter));
  const DemStore* ter_ptr = nter ? &terrain : nullptr;

  TileCache tiles((data / "tiles").string(), cfg.upstream);
  tiles.set_online(!cfg.offline && tiles.fetch_available());

  // Small catalogs are held in RAM.
  const std::string layers_json = read_file(data / "catalog" / "layers.json");
  const std::string bookmarks_json = read_file(data / "catalog" / "bookmarks.json");
  json nomenclature = json::array();
  try {
    auto s = read_file(data / "catalog" / "nomenclature.json");
    if (!s.empty()) nomenclature = json::parse(s);
  } catch (...) {
    log("WARNING: nomenclature.json unreadable");
  }
  json historic = json::object();
  try {
    auto h = read_file(data / "catalog" / "historic_routes.geojson");
    if (!h.empty()) historic = json::parse(h);
  } catch (...) {
    log("WARNING: historic_routes.geojson unreadable");
  }
  const std::string historic_json = historic.is_null() || historic.empty()
      ? std::string(R"({"type":"FeatureCollection","features":[]})") : historic.dump();
  const auto started = std::chrono::steady_clock::now();

  httplib::Server svr;
  g_server = &svr;
  // SO_REUSEADDR only: without SO_REUSEPORT a second instance fails to bind instead of silently
  // sharing the port with an older daemon.
  svr.set_socket_options([](socket_t sock) {
    int yes = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const void*>(&yes), sizeof(yes));
  });
  svr.set_default_headers({{"Access-Control-Allow-Origin", "*"}});
  svr.set_payload_max_length(1 << 20);
  svr.set_exception_handler([](const httplib::Request&, httplib::Response& res,
                               std::exception_ptr ep) {
    try {
      std::rethrow_exception(ep);
    } catch (const std::invalid_argument& e) {
      send_error(res, 400, e.what());
    } catch (const std::exception& e) {
      send_error(res, 500, e.what());
    } catch (...) {
      send_error(res, 500, "unknown error");
    }
  });

  svr.Get("/api/health", [&](const httplib::Request&, httplib::Response& res) {
    json dems = json::array();
    for (auto& g : dem.grids())
      dems.push_back({{"id", g.id}, {"name", g.name}, {"ppd", g.ppd},
                      {"res_m", std::round(g.res_m())}, {"global", g.global},
                      {"lat_max", g.lat_max}, {"lat_min", g.lat_max - g.height / g.ppd},
                      {"lon_min", wrap180(g.lon_min)},
                      {"lon_max", wrap180(g.lon_min + g.width / g.ppd)},
                      {"citation", g.citation}});
    auto up = std::chrono::duration_cast<std::chrono::seconds>(
                  std::chrono::steady_clock::now() - started).count();
    send_json(res, {{"service", "autonav0d"}, {"version", kVersion}, {"status", "ok"},
                    {"uptime_s", up}, {"online", tiles.online()},
                    {"fetch_available", tiles.fetch_available()},
                    {"tiles", {{"hits", tiles.hits.load()}, {"misses", tiles.misses.load()},
                               {"fetched", tiles.fetched.load()}}},
                    {"dems", dems}, {"thermal_inertia", ti_ptr != nullptr},
                    {"terrain_classes", {{"relief", landform.grids().size()}, {"terrain", nter}}},
                    {"comm_coverage", comms.grids().size()},
                    {"nomenclature", nomenclature.size()}, {"rerun_url", cfg.rerun_url}});
  });

  svr.Post("/api/mode", [&](const httplib::Request& req, httplib::Response& res) {
    if (req.has_param("offline")) {
      bool off = req.get_param_value("offline") == "1" || req.get_param_value("offline") == "true";
      if (!off && !tiles.fetch_available()) return send_error(res, 409, "built without libcurl");
      tiles.set_online(!off);
      log(std::string("mode -> ") + (off ? "offline" : "online"));
    }
    send_json(res, {{"online", tiles.online()}});
  });

  // Live rover state pushed by the ROS 2 bridge (or any client); kept in memory only.
  struct {
    std::mutex mu;
    json latest;
    std::deque<std::array<double, 2>> track;
    std::chrono::steady_clock::time_point at;
  } rover;
  svr.Post("/api/rover/state", [&](const httplib::Request& req, httplib::Response& res) {
    json j;
    try {
      j = json::parse(req.body);
    } catch (...) {
      return send_error(res, 400, "body must be JSON");
    }
    if (!j.is_object() || !j.contains("lat") || !j.contains("lon") || !j["lat"].is_number() ||
        !j["lon"].is_number())
      return send_error(res, 400, "lat and lon (numbers) required");
    double lat = j["lat"], lon = j["lon"];
    if (lat < -90 || lat > 90) return send_error(res, 400, "lat out of range");
    j["lon"] = wrap180(lon);
    auto e = dem.elevation(lat, lon);
    if (!j.contains("elev_m") && e.grid) j["elev_m"] = std::round(e.elev);
    std::lock_guard<std::mutex> lk(rover.mu);
    if (rover.track.empty() || haversine_m(rover.track.back()[0], rover.track.back()[1], lat, lon) > 0.5) {
      rover.track.push_back({lat, wrap180(lon)});
      if (rover.track.size() > 5000) rover.track.pop_front();
    }
    rover.latest = j;
    rover.at = std::chrono::steady_clock::now();
    send_json(res, {{"ok", true}, {"track_points", rover.track.size()}});
  });
  svr.Get("/api/rover/state", [&](const httplib::Request& req, httplib::Response& res) {
    std::lock_guard<std::mutex> lk(rover.mu);
    if (rover.latest.is_null()) return send_json(res, {{"state", nullptr}});
    double age = std::chrono::duration<double>(std::chrono::steady_clock::now() - rover.at).count();
    json out = {{"state", rover.latest}, {"age_s", std::round(age * 10) / 10}};
    if (req.get_param_value("track") != "0") {
      json t = json::array();
      for (auto& p : rover.track) t.push_back({p[0], p[1]});
      out["track"] = t;
    }
    send_json(res, out);
  });
  svr.Delete("/api/rover/state", [&](const httplib::Request&, httplib::Response& res) {
    std::lock_guard<std::mutex> lk(rover.mu);
    rover.latest = nullptr;
    rover.track.clear();
    send_json(res, {{"ok", true}});
  });

  svr.Get("/api/layers", [&](const httplib::Request&, httplib::Response& res) {
    if (layers_json.empty()) return send_error(res, 404, "layers.json missing; run fetch_data.py");
    res.set_content(layers_json, "application/json");
  });
  svr.Get("/api/historic", [&](const httplib::Request&, httplib::Response& res) {
    res.set_header("Cache-Control", "max-age=3600");
    res.set_content(historic_json, "application/geo+json");
  });
  svr.Get("/api/bookmarks", [&](const httplib::Request&, httplib::Response& res) {
    res.set_content(bookmarks_json.empty() ? "[]" : bookmarks_json, "application/json");
  });

  svr.Get("/api/nomenclature", [&](const httplib::Request& req, httplib::Response& res) {
    std::string q = req.get_param_value("q");
    for (auto& c : q) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    size_t limit = static_cast<size_t>(qnum(req, "limit", q.empty() ? 5000 : 25));
    json out = json::array();
    for (auto& f : nomenclature) {
      if (out.size() >= limit) break;
      if (!q.empty()) {
        std::string n = f.value("name", "");
        for (auto& c : n) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        if (n.find(q) == std::string::npos) continue;
      }
      out.push_back(f);
    }
    send_json(res, out);
  });

  svr.Get(R"(/api/vector/([a-z0-9_]+))", [&](const httplib::Request& req, httplib::Response& res) {
    auto s = read_file(data / "vector" / (req.matches[1].str() + ".geojson"));
    if (s.empty()) return send_error(res, 404, "vector layer not cached");
    res.set_header("Cache-Control", "max-age=3600");
    res.set_content(s, "application/geo+json");
  });

  svr.Get("/api/elevation", [&](const httplib::Request& req, httplib::Response& res) {
    double lat = qnum(req, "lat", 0), lon = qnum(req, "lon", 0);
    auto s = dem.elevation(lat, lon);
    if (!s.grid) return send_error(res, 404, "no DEM coverage");
    double t = ti_ptr ? ti.elev(lat, lon) : kNoData;
    auto cls = [&](const DemStore& st) -> json {
      const DemGrid* g = nullptr;
      int c = st.class_at(lat, lon, &g);
      if (!c || !g || !g->classes.count(c)) return nullptr;
      auto& ci = g->classes.at(c);
      json o = {{"code", c}, {"name", ci.name}, {"grid", g->id}, {"res_m", std::round(g->res_m())}};
      if (!ci.group.empty()) o["group"] = ci.group;
      if (!ci.code.empty()) o["id"] = ci.code;
      return o;
    };
    send_json(res, {{"lat", lat}, {"lon", lon}, {"elev_m", std::round(s.elev * 10) / 10},
                    {"res_m", std::round(s.res_m)}, {"dem", s.grid->id},
                    {"thermal_inertia", t > kNoData ? json(std::round(t)) : json(nullptr)},
                    {"relief", cls(landform)}, {"terrain", cls(terrain)}, {"comms", cls(comms)}});
  });

  // Elevation profile along a polyline: pts=lat,lon;lat,lon;...  n=samples
  svr.Get("/api/profile", [&](const httplib::Request& req, httplib::Response& res) {
    std::vector<std::pair<double, double>> pts;
    std::istringstream is(req.get_param_value("pts"));
    std::string tok;
    while (std::getline(is, tok, ';')) {
      double la, lo;
      if (!parse_latlon(tok, la, lo)) return send_error(res, 400, "bad point: " + tok);
      pts.push_back({la, lo});
    }
    if (pts.size() < 2 || pts.size() > 500) return send_error(res, 400, "need 2..500 points");
    int n = std::clamp(static_cast<int>(qnum(req, "n", 256)), 2, 2000);
    std::vector<double> seg{0};
    for (size_t i = 1; i < pts.size(); ++i)
      seg.push_back(seg.back() + haversine_m(pts[i - 1].first, pts[i - 1].second, pts[i].first,
                                             pts[i].second));
    double total = seg.back();
    json out = json::array();
    size_t k = 0;
    double prev_e = kNoData, prev_d = 0, maxs = 0;
    for (int i = 0; i < n; ++i) {
      double d = total * i / (n - 1);
      while (k + 2 < pts.size() && seg[k + 1] < d) ++k;
      double t = seg[k + 1] > seg[k] ? (d - seg[k]) / (seg[k + 1] - seg[k]) : 0;
      double la = pts[k].first + t * (pts[k + 1].first - pts[k].first);
      double lo = pts[k].second + t * wrap180(pts[k + 1].second - pts[k].second);
      double e = dem.elev(la, lo);
      double s = 0;
      if (prev_e > kNoData && e > kNoData && d > prev_d)
        s = std::atan(std::fabs(e - prev_e) / (d - prev_d)) / kDeg;
      maxs = std::max(maxs, s);
      out.push_back({{"d", std::round(d)}, {"lat", la}, {"lon", wrap180(lo)},
                     {"elev", e > kNoData ? json(std::round(e)) : json(nullptr)},
                     {"slope", std::round(s * 10) / 10}});
      prev_e = e;
      prev_d = d;
    }
    send_json(res, {{"distance_m", std::round(total)}, {"max_slope_deg", std::round(maxs * 10) / 10},
                    {"samples", out}});
  });

  svr.Get("/api/route", [&](const httplib::Request& req, httplib::Response& res) {
    RouteRequest rq{};
    auto perr = parse_route_request(req, rq);
    if (!perr.empty()) return send_error(res, 400, perr);
    auto t0 = std::chrono::steady_clock::now();
    auto r = plan_route(dem, rq, ti_ptr, ter_ptr, comm_ptr);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::steady_clock::now() - t0).count();
    if (!r.ok) return send_error(res, 422, r.error);
    auto gj = route_to_geojson(r, rq);
    gj["properties"]["compute_ms"] = ms;
    send_json(res, gj);
  });

  // Route comparison: several profiles + alternatives on one shared grid.
  //   /api/routes?from=..&to=..&profiles=safe,wear,eva,shortest&alternatives=1
  svr.Get("/api/routes", [&](const httplib::Request& req, httplib::Response& res) {
    RouteRequest rq{};
    auto perr = parse_route_request(req, rq);
    if (!perr.empty()) return send_error(res, 400, perr);
    std::vector<RouteProfile> profiles;
    std::string list = req.has_param("profiles") ? req.get_param_value("profiles") : "safe,wear,eva,shortest";
    std::istringstream is(list);
    std::string tok;
    while (std::getline(is, tok, ','))
      if (!tok.empty() && profiles.size() < 6) profiles.push_back(parse_profile(tok));
    if (profiles.empty()) return send_error(res, 400, "no profiles");
    int alts = std::clamp(static_cast<int>(qnum(req, "alternatives", 1)), 0, 3);
    if (req.has_param("ref")) {  // score a historic traverse alongside the planned routes
      const std::string ref = req.get_param_value("ref");
      const json* hit = nullptr;
      if (historic.contains("features"))
        for (auto& f : historic["features"])
          if (f.value("id", "") == ref) hit = &f;
      if (!hit) return send_error(res, 404, "unknown historic route: " + ref);
      for (auto& c : (*hit)["geometry"]["coordinates"])
        rq.reference.emplace_back(c[1].get<double>(), c[0].get<double>());
      rq.reference_id = "historic:" + ref;
      rq.reference_label = "Historic · " + (*hit)["properties"].value("name", ref);
    }
    auto t0 = std::chrono::steady_clock::now();
    auto routes = plan_compare(dem, rq, profiles, alts, ti_ptr, ter_ptr, comm_ptr);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::steady_clock::now() - t0).count();
    json feats = json::array(), failed = json::array();
    const RouteResult* best[5] = {nullptr, nullptr, nullptr, nullptr, nullptr};
    auto better = [](const RouteResult* cur, const RouteResult& r, double (*f)(const RouteResult&)) {
      return !cur || f(r) < f(*cur) - 1e-9;
    };
    double (*metric[5])(const RouteResult&) = {
        [](const RouteResult& r) { return r.dist_m; },
        [](const RouteResult& r) { return r.eva_hours; },
        [](const RouteResult& r) { return r.wear_index; },
        [](const RouteResult& r) { return r.rover_energy_wh; },
        [](const RouteResult& r) { return r.max_slope_deg; }};
    for (auto& r : routes) {
      if (!r.ok) {
        failed.push_back({{"id", r.id}, {"label", r.label}, {"error", r.error}});
        continue;
      }
      feats.push_back(route_to_geojson(r, rq));
      for (int m = 0; m < 5; ++m)
        if (better(best[m], r, metric[m])) best[m] = &r;
    }
    if (feats.empty())
      return send_error(res, 422, routes.empty() ? "no route" : routes.front().error);
    static const char* names[5] = {"shortest_distance", "fastest_eva", "least_wear", "least_energy", "gentlest"};
    json bestj = json::object();
    for (int m = 0; m < 5; ++m)
      if (best[m]) bestj[names[m]] = best[m]->id;
    send_json(res, {{"type", "FeatureCollection"}, {"features", feats}, {"failed", failed},
                    {"best", bestj}, {"compute_ms", ms}});
  });

  // Heightmap for the 3D view: /api/terrain?s=&w=&n=&e=&size=256 (rows north->south)
  svr.Get("/api/terrain", [&](const httplib::Request& req, httplib::Response& res) {
    double s = qnum(req, "s", 0), w = qnum(req, "w", 0), n = qnum(req, "n", 0), e = qnum(req, "e", 0);
    if (!(n > s) || !(e > w) || n - s > 30 || e - w > 60 || s < -90 || n > 90)
      return send_error(res, 400, "bad bbox (max 30 x 60 deg)");
    int size = std::clamp(static_cast<int>(qnum(req, "size", 256)), 16, 512);
    double coslat = std::max(0.05, std::cos(0.5 * (s + n) * kDeg));
    double wm = (e - w) * kDeg * kMarsRadiusM * coslat, hm = (n - s) * kDeg * kMarsRadiusM;
    int W = wm >= hm ? size : std::max(16, static_cast<int>(size * wm / hm));
    int H = hm >= wm ? size : std::max(16, static_cast<int>(size * hm / wm));
    std::vector<double> z(static_cast<size_t>(W) * H);
    double lo = 1e30, hi = -1e30, best_res = 1e30;
    for (int y = 0; y < H; ++y)
      for (int x = 0; x < W; ++x) {
        double lat = n - (n - s) * y / (H - 1), lon = w + (e - w) * x / (W - 1);
        auto smp = dem.elevation(lat, lon);
        double v = smp.grid ? smp.elev : std::nan("");
        if (smp.grid) {
          lo = std::min(lo, v);
          hi = std::max(hi, v);
          best_res = std::min(best_res, smp.res_m);
        }
        z[y * W + x] = v;
      }
    if (lo > hi) return send_error(res, 404, "no DEM coverage");
    json elev = json::array();
    for (double v : z) elev.push_back(std::isnan(v) ? lo : std::round(v));
    send_json(res, {{"width", W}, {"height", H}, {"bounds", {s, w, n, e}},
                    {"width_m", std::round(wm)}, {"height_m", std::round(hm)},
                    {"min", std::round(lo)}, {"max", std::round(hi)},
                    {"dem_res_m", std::round(best_res)}, {"elev", elev}});
  });

  svr.Get("/api/sun", [&](const httplib::Request& req, httplib::Response& res) {
    double now = std::chrono::duration<double>(
                     std::chrono::system_clock::now().time_since_epoch()).count();
    double t = qnum(req, "t", now);
    double lat = qnum(req, "lat", 0), lon = qnum(req, "lon", 0);
    auto mt = mars_time_from_unix(t);
    auto sp = sun_position(mt, lat, lon);
    send_json(res, {{"unix", t}, {"lat", lat}, {"lon", lon},
                    {"msd", mt.msd}, {"ls_deg", mt.ls_deg}, {"mst_h", mt.mst_h},
                    {"ltst_h", sp.ltst_h}, {"lmst_h", sp.lmst_h},
                    {"sun_elevation_deg", sp.elevation_deg}, {"sun_azimuth_deg", sp.azimuth_deg},
                    {"declination_deg", mt.declination_deg},
                    {"subsolar_lon_e", wrap180(-mt.subsolar_lon_w)},
                    {"helio_dist_au", mt.helio_dist_au},
                    {"algorithm", "Allison & McEwen (2000) / NASA GISS Mars24"}});
  });

  svr.Get(R"(/tiles/([A-Za-z0-9_.\-]+)/(\d+)/(\d+)/(\d+)\.(png|jpg))",
          [&](const httplib::Request& req, httplib::Response& res) {
            std::string blob;
            bool net = false;
            const auto& m = req.matches;
            if (!tiles.get(m[1], std::stoi(m[2]), std::stoi(m[3]), std::stoi(m[4]), m[5], blob,
                           &net)) {
              res.status = 404;
              return;
            }
            res.set_header("Cache-Control", "max-age=86400");
            res.set_header("X-AutoNav-Cache", net ? "fill" : "hit");
            res.set_content(blob, m[5] == "png" ? "image/png" : "image/jpeg");
          });

  svr.Get(R"(/dem/(hillshade|slope|contours)/(\d+)/(\d+)/(\d+)\.png)",
          [&](const httplib::Request& req, httplib::Response& res) {
            RenderOptions o;
            o.kind = req.matches[1];
            o.sun_az_deg = qnum(req, "az", 315);
            o.sun_el_deg = qnum(req, "el", 35);
            o.shadows = req.get_param_value("shadows") != "0";
            o.max_slope_deg = qnum(req, "max_slope", 25);
            o.contour_interval_m = qnum(req, "interval", 0);
            auto png = render_tile(dem, std::stoi(req.matches[2]), std::stoi(req.matches[3]),
                                   std::stoi(req.matches[4]), o);
            if (png.empty()) { res.status = 404; return; }
            res.set_header("Cache-Control", "max-age=600");
            res.set_content(png, "image/png");
          });

  svr.Get(R"(/dem/(relief|terrain|comms)/(\d+)/(\d+)/(\d+)\.png)",
          [&](const httplib::Request& req, httplib::Response& res) {
            const DemStore& st = req.matches[1] == "relief" ? landform
                                 : req.matches[1] == "comms" ? comms : terrain;
            auto png = render_class_tile(st, std::stoi(req.matches[2]), std::stoi(req.matches[3]),
                                         std::stoi(req.matches[4]));
            if (png.empty()) { res.status = 404; return; }
            res.set_header("Cache-Control", "max-age=600");
            res.set_content(png, "image/png");
          });

  // Legend / metadata for terrain class layers.
  svr.Get("/api/terrain/classes", [&](const httplib::Request&, httplib::Response& res) {
    auto describe = [](const DemStore& st) {
      json arr = json::array();
      for (auto& g : st.grids()) {
        json cls = json::object();
        for (auto& [k, v] : g.classes) {
          json c = {{"name", v.name}, {"color", v.color}, {"group", v.group}};
          if (!v.code.empty()) c["id"] = v.code;
          if (v.rock >= 0 || v.soft >= 0) c["planner"] = {{"rock", v.rock}, {"soft", v.soft}};
          if (v.comm >= 0) c["comm_loss"] = v.comm;
          cls[std::to_string(k)] = c;
        }
        arr.push_back({{"id", g.id}, {"name", g.name}, {"level", g.level}, {"res_m", std::round(g.res_m())},
                       {"lat_max", g.lat_max}, {"lat_min", g.lat_max - g.height / g.ppd},
                       {"lon_min", wrap180(g.lon_min)}, {"lon_max", wrap180(g.lon_min + g.width / g.ppd)},
                       {"citation", g.citation}, {"classes", cls}});
      }
      return arr;
    };
    send_json(res, {{"relief", describe(landform)}, {"terrain", describe(terrain)},
                    {"comms", describe(comms)}});
  });

  if (!cfg.web_dir.empty()) {
    // no-cache: browsers must revalidate UI assets, otherwise updates appear "not working".
    if (!svr.set_mount_point("/", cfg.web_dir, {{"Cache-Control", "no-cache"}})) log("WARNING: web dir not found: " + cfg.web_dir);
  }

  svr.set_logger([](const httplib::Request& req, const httplib::Response& res) {
    if (res.status >= 400 && req.path.rfind("/tiles/", 0) != 0)
      log(req.method + " " + req.path + " -> " + std::to_string(res.status));
  });

  std::signal(SIGINT, [](int) { if (g_server) g_server->stop(); });
  std::signal(SIGTERM, [](int) { if (g_server) g_server->stop(); });
  std::signal(SIGPIPE, SIG_IGN);

  const std::string mode = tiles.online() ? " (online tile fill)" : " (offline)";
  std::string where;
  bool bound;
  if (!cfg.unix_socket.empty()) {
    ::unlink(cfg.unix_socket.c_str());
    svr.set_address_family(AF_UNIX);
    where = "unix:" + cfg.unix_socket;
    bound = svr.bind_to_port(cfg.unix_socket, 80);
  } else {
    where = "http://" + cfg.host + ":" + std::to_string(cfg.port);
    bound = svr.bind_to_port(cfg.host, cfg.port);
  }
  if (!bound) {
    log("cannot bind " + where + " (already in use?)");
    return 1;
  }
  log("listening on " + where + mode);
  bool ok = svr.listen_after_bind();
  if (!cfg.unix_socket.empty()) ::unlink(cfg.unix_socket.c_str());
  if (!ok) {
    log("server error");
    return 1;
  }
  log("stopped");
  return 0;
}

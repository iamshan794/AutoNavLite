// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — Mars time & solar position.
// Algorithm: Allison & McEwen (2000), as documented by NASA GISS Mars24
// https://www.giss.nasa.gov/tools/mars24/help/algorithm.html
#pragma once

namespace autonav {

struct MarsTime {
  double jd_ut = 0, jd_tt = 0, dt_j2000 = 0;
  double ls_deg = 0;          // areocentric solar longitude (season)
  double eot_deg = 0;         // equation of time
  double mst_h = 0;           // Airy mean time (prime meridian)
  double msd = 0;             // Mars Sol Date
  double subsolar_lon_w = 0;  // planetographic, degrees west
  double declination_deg = 0;
  double helio_dist_au = 0;
};

struct SunPos {
  double elevation_deg = 0;
  double azimuth_deg = 0;  // clockwise from north
  double ltst_h = 0;       // local true solar time
  double lmst_h = 0;       // local mean solar time
};

double tt_minus_utc(double jd_ut);
MarsTime mars_time_from_jd(double jd_ut);
MarsTime mars_time_from_unix(double unix_seconds);
// lon_east in degrees (any range), lat planetographic degrees.
SunPos sun_position(const MarsTime& t, double lat, double lon_east);

}  // namespace autonav

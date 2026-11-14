// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "mars_time.hpp"

#include <cmath>

namespace autonav {
namespace {
constexpr double kD = 3.14159265358979323846 / 180.0;
double mod(double x, double m) {
  double r = std::fmod(x, m);
  return r < 0 ? r + m : r;
}
}  // namespace

double tt_minus_utc(double jd_ut) {
  // TAI-UTC leap-second table (IERS), JD of effective date. TT = TAI + 32.184 s.
  static const struct { double jd; double tai_utc; } kTable[] = {
      {2441317.5, 10}, {2441499.5, 11}, {2441683.5, 12}, {2442048.5, 13}, {2442413.5, 14},
      {2442778.5, 15}, {2443144.5, 16}, {2443509.5, 17}, {2443874.5, 18}, {2444239.5, 19},
      {2444786.5, 20}, {2445151.5, 21}, {2445516.5, 22}, {2446247.5, 23}, {2447161.5, 24},
      {2447892.5, 25}, {2448257.5, 26}, {2448804.5, 27}, {2449169.5, 28}, {2449534.5, 29},
      {2450083.5, 30}, {2450630.5, 31}, {2451179.5, 32}, {2453736.5, 33}, {2454832.5, 34},
      {2456109.5, 35}, {2457204.5, 36}, {2457754.5, 37}};
  if (jd_ut < kTable[0].jd) {
    double T = (jd_ut - 2451545.0) / 36525.0;
    return 64.184 + 59 * T - 51.2 * T * T - 67.1 * T * T * T - 16.4 * T * T * T * T;
  }
  double v = 10;
  for (auto& e : kTable)
    if (jd_ut >= e.jd) v = e.tai_utc;
  return v + 32.184;
}

MarsTime mars_time_from_jd(double jd_ut) {
  MarsTime t;
  t.jd_ut = jd_ut;
  t.jd_tt = jd_ut + tt_minus_utc(jd_ut) / 86400.0;
  const double d = t.dt_j2000 = t.jd_tt - 2451545.0;

  double M = mod(19.3871 + 0.52402073 * d, 360.0);
  double afms = mod(270.3871 + 0.524038496 * d, 360.0);
  static const double A[] = {0.0071, 0.0057, 0.0039, 0.0037, 0.0021, 0.0020, 0.0018};
  static const double tau[] = {2.2353, 2.7543, 1.1177, 15.7866, 2.1354, 2.4694, 32.8493};
  static const double phi[] = {49.409, 168.173, 191.837, 21.736, 15.704, 95.528, 49.095};
  double pbs = 0;
  for (int i = 0; i < 7; ++i) pbs += A[i] * std::cos((0.985626 * d / tau[i] + phi[i]) * kD);
  double Mr = M * kD;
  double v_m = (10.691 + 3.0e-7 * d) * std::sin(Mr) + 0.623 * std::sin(2 * Mr) +
               0.050 * std::sin(3 * Mr) + 0.005 * std::sin(4 * Mr) +
               0.0005 * std::sin(5 * Mr) + pbs;
  t.ls_deg = mod(afms + v_m, 360.0);
  double Ls = t.ls_deg * kD;
  t.eot_deg = 2.861 * std::sin(2 * Ls) - 0.071 * std::sin(4 * Ls) +
              0.002 * std::sin(6 * Ls) - v_m;
  t.msd = (t.jd_tt - 2451549.5) / 1.0274912517 + 44796.0 - 0.0009626;
  t.mst_h = mod(24.0 * t.msd, 24.0);
  t.subsolar_lon_w = mod(t.mst_h * 15.0 + t.eot_deg + 180.0, 360.0);
  t.declination_deg = std::asin(0.42565 * std::sin(Ls)) / kD + 0.25 * std::sin(Ls);
  t.helio_dist_au = 1.52367934 * (1.00436 - 0.09309 * std::cos(Mr) - 0.004336 * std::cos(2 * Mr) -
                                  0.00031 * std::cos(3 * Mr) - 0.00003 * std::cos(4 * Mr));
  return t;
}

MarsTime mars_time_from_unix(double s) { return mars_time_from_jd(2440587.5 + s / 86400.0); }

SunPos sun_position(const MarsTime& t, double lat, double lon_east) {
  SunPos p;
  double lon_w = mod(-lon_east, 360.0);
  p.lmst_h = mod(t.mst_h - lon_w / 15.0, 24.0);
  p.ltst_h = mod(p.lmst_h + t.eot_deg / 15.0, 24.0);
  double H = (lon_w - t.subsolar_lon_w) * kD;
  double phi = lat * kD, dec = t.declination_deg * kD;
  double cz = std::sin(dec) * std::sin(phi) + std::cos(dec) * std::cos(phi) * std::cos(H);
  p.elevation_deg = 90.0 - std::acos(std::fmax(-1.0, std::fmin(1.0, cz))) / kD;
  p.azimuth_deg = mod(
      std::atan2(std::sin(H), std::cos(phi) * std::tan(dec) - std::sin(phi) * std::cos(H)) / kD,
      360.0);
  return p;
}

}  // namespace autonav

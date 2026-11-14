// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// AutoNav-0 — offline-first WMTS tile cache (disk) with optional upstream fill from NASA Trek.
#pragma once
#include <atomic>
#include <mutex>
#include <string>
#include <unordered_set>

namespace autonav {

class TileCache {
 public:
  TileCache(std::string root, std::string upstream);
  // Returns true and fills `out` if tile available (disk, or fetched when online).
  bool get(const std::string& layer, int z, int row, int col, const std::string& ext,
           std::string& out, bool* from_net = nullptr);
  void set_online(bool on) { online_ = on; }
  bool online() const { return online_; }
  bool fetch_available() const;
  static bool valid_layer(const std::string& s);

  std::atomic<long> hits{0}, misses{0}, fetched{0};

 private:
  bool fetch(const std::string& url, std::string& out);
  std::string root_, upstream_;
  std::atomic<bool> online_{true};
  std::mutex neg_mu_;
  std::unordered_set<std::string> negative_;  // known-missing upstream tiles
};

}  // namespace autonav

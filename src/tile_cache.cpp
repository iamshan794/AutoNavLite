// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
#include "tile_cache.hpp"

#include <cctype>
#include <filesystem>
#include <fstream>
#include <sstream>

#ifdef AUTONAV_WITH_CURL
#include <curl/curl.h>
#endif

namespace autonav {
namespace fs = std::filesystem;

TileCache::TileCache(std::string root, std::string upstream)
    : root_(std::move(root)), upstream_(std::move(upstream)) {
#ifdef AUTONAV_WITH_CURL
  curl_global_init(CURL_GLOBAL_DEFAULT);
#else
  online_ = false;
#endif
}

bool TileCache::fetch_available() const {
#ifdef AUTONAV_WITH_CURL
  return true;
#else
  return false;
#endif
}

bool TileCache::valid_layer(const std::string& s) {
  if (s.empty() || s.size() > 128 || s.find("..") != std::string::npos) return false;
  for (char c : s)
    if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '-' || c == '.'))
      return false;
  return true;
}

bool TileCache::get(const std::string& layer, int z, int row, int col, const std::string& ext,
                    std::string& out, bool* from_net) {
  if (from_net) *from_net = false;
  if (!valid_layer(layer) || (ext != "png" && ext != "jpg")) return false;
  std::ostringstream rel;
  rel << layer << '/' << z << '/' << row << '/' << col << '.' << ext;
  fs::path p = fs::path(root_) / rel.str();
  {
    std::ifstream f(p, std::ios::binary);
    if (f) {
      out.assign(std::istreambuf_iterator<char>(f), {});
      ++hits;
      return true;
    }
  }
  ++misses;
  if (!online_) return false;
  {
    std::lock_guard<std::mutex> lk(neg_mu_);
    if (negative_.count(rel.str())) return false;
  }
  std::ostringstream url;
  url << upstream_ << '/' << layer << "/1.0.0/default/default028mm/" << z << '/' << row << '/'
      << col << '.' << ext;
  if (!fetch(url.str(), out)) {
    std::lock_guard<std::mutex> lk(neg_mu_);
    if (negative_.size() > 100000) negative_.clear();
    negative_.insert(rel.str());
    return false;
  }
  std::error_code ec;
  fs::create_directories(p.parent_path(), ec);
  fs::path tmp = p;
  tmp += ".part";
  {
    std::ofstream f(tmp, std::ios::binary);
    f.write(out.data(), static_cast<std::streamsize>(out.size()));
  }
  fs::rename(tmp, p, ec);
  ++fetched;
  if (from_net) *from_net = true;
  return true;
}

#ifdef AUTONAV_WITH_CURL
static size_t write_cb(char* ptr, size_t sz, size_t n, void* ud) {
  static_cast<std::string*>(ud)->append(ptr, sz * n);
  return sz * n;
}
#endif

bool TileCache::fetch(const std::string& url, std::string& out) {
#ifdef AUTONAV_WITH_CURL
  CURL* c = curl_easy_init();
  if (!c) return false;
  out.clear();
  curl_easy_setopt(c, CURLOPT_URL, url.c_str());
  curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, write_cb);
  curl_easy_setopt(c, CURLOPT_WRITEDATA, &out);
  curl_easy_setopt(c, CURLOPT_USERAGENT, "autonav0d/0.1 (offline tile cache)");
  curl_easy_setopt(c, CURLOPT_FOLLOWLOCATION, 1L);
  curl_easy_setopt(c, CURLOPT_CONNECTTIMEOUT, 5L);
  curl_easy_setopt(c, CURLOPT_TIMEOUT, 20L);
  curl_easy_setopt(c, CURLOPT_NOSIGNAL, 1L);
  CURLcode rc = curl_easy_perform(c);
  long code = 0;
  char* ctype = nullptr;
  curl_easy_getinfo(c, CURLINFO_RESPONSE_CODE, &code);
  curl_easy_getinfo(c, CURLINFO_CONTENT_TYPE, &ctype);
  bool ok = rc == CURLE_OK && code == 200 && !out.empty() && ctype &&
            std::string(ctype).rfind("image/", 0) == 0;
  curl_easy_cleanup(c);
  return ok;
#else
  (void)url;
  (void)out;
  return false;
#endif
}

}  // namespace autonav

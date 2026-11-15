// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// autonav_gate — always-on entry screen for AutoNav-0.
//
// Runs on small, always-powered compute and serves the entry page (thumbnail + Enter). "Enter" wakes the
// bigger compute that runs autonav0d, waits until its /api/health answers, then the browser is redirected
// to the app. The app's Exit button returns here and puts the app back to sleep; the gate keeps running.
//
// Two ways to wake/sleep the app:
//   --app-cmd CMD                 child-process mode (localhost / one box): the gate starts CMD on wake
//                                 and stops it (SIGTERM, then SIGKILL) on sleep.
//   --wake-cmd CMD --sleep-cmd CMD  external mode (e.g. start/stop a cloud VM or container); the commands
//                                 run in the background and the gate polls --app-url for readiness.
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

#include "httplib/httplib.h"
#include "nlohmann/json.hpp"

namespace {
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

struct Config {
  std::string host = "127.0.0.1";
  int port = 8000;
  std::string web_dir = "web/entry";
  std::string app_url = "http://127.0.0.1:8080";  // where the gate probes /api/health
  std::string public_app_url = "http://{host}:8080/";  // where browsers are sent ({host} = page host)
  std::string app_cmd, wake_cmd, sleep_cmd;
  int wake_timeout_s = 180;
};

void log(const std::string& m) { std::cerr << "autonav_gate: " << m << std::endl; }

void usage() {
  std::cout << "autonav_gate - always-on AutoNav-0 entry screen (wakes / sleeps the app)\n\n"
            << "  --host ADDR          bind address (default 127.0.0.1)\n"
            << "  --port N             port (default 8000)\n"
            << "  --web DIR            entry page directory (default web/entry)\n"
            << "  --app-url URL        app base URL the gate probes (default http://127.0.0.1:8080)\n"
            << "  --public-app-url URL app URL for browsers, {host} = entry page host\n"
            << "                       (default http://{host}:8080/)\n"
            << "  --app-cmd CMD        child-process mode: start CMD on wake, stop it on sleep\n"
            << "  --wake-cmd CMD       external mode: run CMD to wake the app compute\n"
            << "  --sleep-cmd CMD      external mode: run CMD to put it to sleep\n"
            << "  --wake-timeout S     give up waiting for the app after S seconds (default 180)\n";
}

class AppControl {
 public:
  explicit AppControl(const Config& c) : cfg_(c) {}

  // Probes the app health endpoint (short timeouts; the gate must stay responsive).
  bool healthy() const {
    httplib::Client cli(cfg_.app_url);
    cli.set_connection_timeout(1, 0);
    cli.set_read_timeout(2, 0);
    auto r = cli.Get("/api/health");
    return r && r->status == 200;
  }

  std::string state() {
    std::lock_guard<std::mutex> lk(mu_);
    reap_locked();
    bool up = healthy();
    if (state_ == "sleeping") {
      if (!up && !child_running_locked()) state_ = "asleep";
    } else if (up) {
      state_ = "awake";
    } else if (state_ == "waking") {
      if (!cfg_.app_cmd.empty() && !child_running_locked()) {
        state_ = "error";
        error_ = "app process exited during start-up (see gate log)";
      } else if (Clock::now() - wake_started_ > std::chrono::seconds(cfg_.wake_timeout_s)) {
        state_ = "error";
        error_ = "app did not become healthy within " + std::to_string(cfg_.wake_timeout_s) + " s";
      }
    } else if (state_ == "awake") {
      state_ = "asleep";  // stopped or crashed outside the gate
    }
    return state_;
  }

  std::string error() {
    std::lock_guard<std::mutex> lk(mu_);
    return error_;
  }

  void wake() {
    std::lock_guard<std::mutex> lk(mu_);
    reap_locked();
    if (state_ == "waking" || (state_ == "awake" && healthy())) return;
    error_.clear();
    state_ = "waking";
    wake_started_ = Clock::now();
    if (!cfg_.app_cmd.empty()) {
      if (!child_running_locked()) child_ = spawn(cfg_.app_cmd);
    } else if (!cfg_.wake_cmd.empty()) {
      run_detached(cfg_.wake_cmd);
    }
    log("wake requested");
  }

  void sleep() {
    std::lock_guard<std::mutex> lk(mu_);
    state_ = "sleeping";
    if (!cfg_.app_cmd.empty()) {
      stop_child_locked();
    } else if (!cfg_.sleep_cmd.empty()) {
      run_detached(cfg_.sleep_cmd);
    }
    log("sleep requested");
  }

  void shutdown() {
    std::lock_guard<std::mutex> lk(mu_);
    if (!cfg_.app_cmd.empty()) stop_child_locked();
  }

  std::string mode() const {
    return !cfg_.app_cmd.empty() ? "child" : !cfg_.wake_cmd.empty() ? "external" : "monitor";
  }

 private:
  static pid_t spawn(const std::string& cmd) {
    pid_t pid = fork();
    if (pid == 0) {
      setsid();  // own process group so sleep() can stop the whole tree
      execl("/bin/sh", "sh", "-c", cmd.c_str(), static_cast<char*>(nullptr));
      _exit(127);
    }
    if (pid < 0) log("fork failed");
    return pid;
  }

  static void run_detached(const std::string& cmd) {
    pid_t pid = spawn(cmd);
    if (pid > 0) std::thread([pid] { waitpid(pid, nullptr, 0); }).detach();
  }

  bool child_running_locked() const { return child_ > 0; }

  void reap_locked() {
    if (child_ > 0 && waitpid(child_, nullptr, WNOHANG) == child_) {
      log("app process exited");
      child_ = -1;
    }
  }

  void stop_child_locked() {
    if (child_ <= 0) return;
    kill(-child_, SIGTERM);
    for (int i = 0; i < 50; ++i) {  // up to 5 s for a clean stop
      if (waitpid(child_, nullptr, WNOHANG) == child_) {
        child_ = -1;
        return;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    kill(-child_, SIGKILL);
    waitpid(child_, nullptr, 0);
    child_ = -1;
  }

  const Config& cfg_;
  std::mutex mu_;
  std::string state_ = "asleep", error_;
  pid_t child_ = -1;
  Clock::time_point wake_started_{};
};

httplib::Server* g_server = nullptr;
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
    else if (a == "--web") cfg.web_dir = next();
    else if (a == "--app-url") cfg.app_url = next();
    else if (a == "--public-app-url") cfg.public_app_url = next();
    else if (a == "--app-cmd") cfg.app_cmd = next();
    else if (a == "--wake-cmd") cfg.wake_cmd = next();
    else if (a == "--sleep-cmd") cfg.sleep_cmd = next();
    else if (a == "--wake-timeout") cfg.wake_timeout_s = std::stoi(next());
    else if (a == "-h" || a == "--help") { usage(); return 0; }
    else { std::cerr << "unknown option " << a << "\n"; usage(); return 2; }
  }

  AppControl app(cfg);
  httplib::Server svr;
  g_server = &svr;

  svr.Get("/healthz", [](const httplib::Request&, httplib::Response& res) {
    res.set_content("ok", "text/plain");
  });
  svr.Get("/api/status", [&](const httplib::Request&, httplib::Response& res) {
    auto st = app.state();
    json j = {{"state", st}, {"mode", app.mode()}, {"app_url", cfg.public_app_url}};
    if (st == "error") j["error"] = app.error();
    res.set_header("Cache-Control", "no-store");
    res.set_content(j.dump(), "application/json");
  });
  svr.Post("/api/wake", [&](const httplib::Request&, httplib::Response& res) {
    if (app.mode() == "monitor" && !app.healthy()) {
      res.status = 409;
      res.set_content(R"({"error":"app is down and the gate has no --app-cmd / --wake-cmd"})", "application/json");
      return;
    }
    app.wake();
    res.set_content(json{{"state", app.state()}}.dump(), "application/json");
  });
  svr.Post("/api/sleep", [&](const httplib::Request&, httplib::Response& res) {
    if (app.mode() != "monitor") app.sleep();
    res.set_content(json{{"state", app.state()}}.dump(), "application/json");
  });
  if (!svr.set_mount_point("/", cfg.web_dir, {{"Cache-Control", "no-cache"}}))
    log("WARNING: entry page dir not found: " + cfg.web_dir);

  std::signal(SIGINT, [](int) { if (g_server) g_server->stop(); });
  std::signal(SIGTERM, [](int) { if (g_server) g_server->stop(); });
  std::signal(SIGPIPE, SIG_IGN);

  if (!svr.bind_to_port(cfg.host, cfg.port)) {
    log("cannot bind " + cfg.host + ":" + std::to_string(cfg.port) + " (already in use?)");
    return 1;
  }
  log("entry screen on http://" + cfg.host + ":" + std::to_string(cfg.port) + " (" + app.mode() +
      " mode, app " + cfg.app_url + ")");
  svr.listen_after_bind();
  app.shutdown();
  log("stopped");
  return 0;
}

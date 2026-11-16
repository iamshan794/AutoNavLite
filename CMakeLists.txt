# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
cmake_minimum_required(VERSION 3.16)
project(autonav0 VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(AUTONAV_WITH_CURL "Fetch missing tiles from NASA Trek when online" ON)
option(AUTONAV_BUILD_TESTS "Build unit tests" ON)

find_package(Threads REQUIRED)

add_library(autonav_core STATIC
  src/comms.cpp src/dem.cpp src/mars_time.cpp src/planner.cpp src/render.cpp src/terrain.cpp src/tile_cache.cpp)
target_include_directories(autonav_core PUBLIC src third_party)
target_compile_options(autonav_core PRIVATE -Wall -Wextra)
target_link_libraries(autonav_core PUBLIC Threads::Threads)

if(AUTONAV_WITH_CURL)
  find_package(CURL)
  if(CURL_FOUND)
    target_compile_definitions(autonav_core PRIVATE AUTONAV_WITH_CURL)
    target_link_libraries(autonav_core PUBLIC CURL::libcurl)
  else()
    message(WARNING "libcurl not found: building offline-only (no upstream tile fill)")
  endif()
endif()

add_executable(autonav0d src/main.cpp)
target_link_libraries(autonav0d PRIVATE autonav_core)

# Always-on entry screen that wakes / sleeps autonav0d (tiny: HTTP + process control only).
add_executable(autonav_gate src/gate.cpp)

find_package(OpenSSL REQUIRED)
target_compile_definitions(autonav_gate PRIVATE CPPHTTPLIB_OPENSSL_SUPPORT)
target_link_libraries(autonav_gate PRIVATE OpenSSL::SSL OpenSSL::Crypto)

target_include_directories(autonav_gate PRIVATE third_party)
target_compile_options(autonav_gate PRIVATE -Wall -Wextra)
target_link_libraries(autonav_gate PRIVATE Threads::Threads)

if(AUTONAV_BUILD_TESTS)
  enable_testing()
  add_executable(autonav_tests tests/test_core.cpp)
  target_link_libraries(autonav_tests PRIVATE autonav_core)
  add_test(NAME core COMMAND autonav_tests)
endif()

include(GNUInstallDirs)
install(TARGETS autonav0d RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(DIRECTORY web/ DESTINATION ${CMAKE_INSTALL_DATADIR}/autonav0/web)

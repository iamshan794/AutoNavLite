# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
# autonav0d — headless Mars map / route service + 2D/3D web client.
#   docker build -t autonav0d .                      # data mounted at runtime (./data:/data)
#   docker build -t autonav0d --build-arg BAKE_DATA=1 .   # self-contained image (GKE, ~60 MB data)

FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake libcurl4-openssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY CMakeLists.txt ./
COPY src src
COPY tests tests
COPY third_party third_party
COPY web web
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j"$(nproc)" \
    && ctest --test-dir build --output-on-failure

FROM ubuntu:24.04 AS data
ARG BAKE_DATA=0
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/fetch_data.py /src/scripts/fetch_data.py
RUN mkdir -p /data && if [ "$BAKE_DATA" = "1" ]; then AUTONAV_DATA=/data python3 /src/scripts/fetch_data.py; fi

FROM ubuntu:24.04 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libcurl4t64 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10001 -M -s /usr/sbin/nologin autonav
COPY --from=build /src/build/autonav0d /src/build/autonav_gate /usr/local/bin/
COPY web /opt/autonav0/web
COPY --from=data --chown=10001:10001 /data /data
USER 10001
EXPOSE 8080 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/health >/dev/null || exit 1
# Default: the app alone (e.g. on the big, wake-on-demand compute). compose.yaml runs autonav_gate instead,
# which serves the always-on entry screen on 8000 and starts this same command on Enter.
ENTRYPOINT ["autonav0d", "--host", "0.0.0.0", "--port", "8080", "--data", "/data", "--web", "/opt/autonav0/web"]

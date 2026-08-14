FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN cmake -S . -B build -DTRUTH_BUILD_TESTS=ON && cmake --build build --parallel && ctest --test-dir build --output-on-failure
CMD ["/src/build/truth_ai_demo"]

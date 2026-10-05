#!/bin/sh
# Preloads jemalloc if available (AIPLAT-1392): glibc's default allocator
# creates per-thread arenas that don't return freed memory to the OS,
# which compounds under MLPA's bursty run_in_threadpool usage (App Attest
# registration, Play Integrity renewal, FxA auth). jemalloc doesn't have
# this behavior. Resolved at runtime rather than hardcoded since the
# library path differs by architecture (amd64 vs arm64).
set -e
mkdir -p "$(dirname "${MEMRAY_OUTPUT:-/tmp/memray/mlpa.bin}")"
JEMALLOC_PATH=$(ldconfig -p | grep libjemalloc.so.2 | awk '{print $NF}' | head -1)
if [ -n "$JEMALLOC_PATH" ]; then
    export LD_PRELOAD="$JEMALLOC_PATH"
fi
# Memory profiling (AIPLAT-1392, stage only): runs MLPA under memray, which
# records every allocation incl. native ones. Analyze a copy of the file with
# `memray flamegraph --leaks` (works on a still-growing file).
if [ "${MEMRAY_ENABLED:-false}" = "true" ]; then
    exec memray run -q -f --native -o "${MEMRAY_OUTPUT:-/tmp/memray/mlpa.bin}" /usr/local/bin/mlpa
fi
exec /usr/local/bin/mlpa

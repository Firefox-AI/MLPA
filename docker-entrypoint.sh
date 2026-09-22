#!/bin/sh
# Preloads jemalloc if available (AIPLAT-1392): glibc's default allocator
# creates per-thread arenas that don't return freed memory to the OS,
# which compounds under MLPA's bursty run_in_threadpool usage (App Attest
# registration, Play Integrity renewal, FxA auth). jemalloc doesn't have
# this behavior. Resolved at runtime rather than hardcoded since the
# library path differs by architecture (amd64 vs arm64).
set -e
JEMALLOC_PATH=$(ldconfig -p | grep libjemalloc.so.2 | awk '{print $NF}' | head -1)
if [ -n "$JEMALLOC_PATH" ]; then
    export LD_PRELOAD="$JEMALLOC_PATH"
fi
exec /usr/local/bin/mlpa

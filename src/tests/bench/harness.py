"""Helpers for writing auth benchmarks. See the README "Auth benchmarks" section."""

import asyncio
import importlib
import os

import pytest

# Rounds per benchmark. On CI the noise between whole runs outweighs the noise
# within a run, so more rounds buy little; scripts/bench_compare.py repeats
# whole runs instead (BENCH_RUNS).
ROUNDS = 50
WARMUP_ROUNDS = 5

# Set by scripts/bench_compare.py when running against the base branch.
IS_BASELINE = os.environ.get("MLPA_BENCH_BASELINE") == "1"


def require(module: str, name: str):
    """Import `module.name`, skipping on the baseline if it does not exist yet.

    A new bench can target code that only exists on the PR. On the baseline
    that means "no baseline", not a failure. On the PR side it must exist.
    """
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as e:
        if IS_BASELINE:
            pytest.skip(f"no baseline: {module}.{name} ({e})")
        raise


def run_async(
    benchmark, loop, make_coro, ops: int, setup=None, concurrent: bool = False
):
    """Benchmark `ops` awaits of `make_coro(i)` per round.

    They run one after another, or all at once with `concurrent=True`.
    `setup` runs before every round, outside the timed section.
    """

    async def batch():
        if concurrent:
            await asyncio.gather(*(make_coro(i) for i in range(ops)))
            return
        for i in range(ops):
            await make_coro(i)

    benchmark.extra_info["operations_per_round"] = ops
    benchmark.pedantic(
        lambda: loop.run_until_complete(batch()),
        setup=setup,
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )


def run_sync(benchmark, fn, ops: int):
    """Benchmark `ops` sequential calls of `fn(i)` per round."""

    def batch():
        for i in range(ops):
            fn(i)

    benchmark.extra_info["operations_per_round"] = ops
    benchmark.pedantic(batch, rounds=ROUNDS, warmup_rounds=WARMUP_ROUNDS)

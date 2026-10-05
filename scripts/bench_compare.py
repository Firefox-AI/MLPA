"""Compare the auth benchmarks between a base ref (default origin/main) and this checkout.

Each side gets its own virtualenv synced from its own uv.lock, so dependency
bumps are measured too. The base side runs this checkout's src/tests/bench, so
both sides run identical bench code. Runs alternate between the sides to
spread CI noise evenly, and each bench's per-operation time is the median of
its per-run medians.

    uv run python scripts/bench_compare.py [--base REF] [--runs N] [--threshold 20%]

Exits 1 if any bench is more than the threshold slower, if a bench fails on
this checkout, or if no results were produced.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

BENCH_PATH = Path("src/tests/bench")
DEFAULT_RUNS = 5
DEFAULT_THRESHOLD = "20%"
SIDES = ("base", "pr")
# `uv run` exports the path of the uv binary it is running as $UV.
UV = os.environ.get("UV") or "uv"


@dataclass
class Row:
    name: str
    base: float | None
    pr: float | None
    status: str  # ok, slower, failed, new

    @property
    def change(self) -> float | None:
        if self.base is None or self.pr is None:
            return None
        return self.pr / self.base - 1


def run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=check, **kwargs)


def parse_threshold(value: str | None) -> float:
    value = (value or DEFAULT_THRESHOLD).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)%?", value)
    if not match:
        sys.exit(f"invalid threshold {value!r}; expected e.g. 20%")
    return float(match.group(1)) / 100


def bench_requirements(repo: Path) -> list[str]:
    """The pins from this checkout's `bench` group, installed on both sides."""
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    return pyproject["dependency-groups"]["bench"]


def prepare_side(root: Path, venv: Path, python: str, requirements: list[str]):
    env = os.environ | {"UV_PROJECT_ENVIRONMENT": str(venv)}
    run(
        [UV, "sync", "--frozen", "--python", python, "--group", "test"],
        cwd=root,
        env=env,
    )
    run(
        [UV, "pip", "install", "--python", str(venv / "bin" / "python"), *requirements],
        cwd=root,
    )


def run_benches(root: Path, venv: Path, out: Path, baseline: bool) -> int:
    env = os.environ | {"MLPA_BENCH_BASELINE": "1" if baseline else "0"}
    cmd = [
        str(venv / "bin" / "python"),
        "-m",
        "pytest",
        str(BENCH_PATH),
        "--benchmark-only",
        "-q",
        "-rsfE",
        "-p",
        "no:cacheprovider",
        "--benchmark-columns=min,median,max,rounds",
        "--benchmark-sort=name",
        f"--benchmark-json={out}",
    ]
    return run(cmd, check=False, cwd=root, env=env).returncode


def per_op_medians(path: Path) -> dict[str, float]:
    """Bench name -> median seconds per operation, for one run."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        b["name"]: b["stats"]["median"] / b["extra_info"].get("operations_per_round", 1)
        for b in data["benchmarks"]
    }


def compare(results: dict[str, list[dict[str, float]]], threshold: float) -> list[Row]:
    names = sorted({name for runs in results.values() for r in runs for name in r})
    rows = []
    for name in names:
        base_runs = [r[name] for r in results["base"] if name in r]
        pr_runs = [r[name] for r in results["pr"] if name in r]
        base = statistics.median(base_runs) if base_runs else None
        pr = statistics.median(pr_runs) if pr_runs else None
        if len(pr_runs) < len(results["pr"]):
            status = "failed"
        elif base is None:
            status = "new"
        elif pr is not None and pr / base - 1 > threshold:
            status = "slower"
        else:
            status = "ok"
        rows.append(Row(name, base, pr, status))
    return rows


def fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    us = seconds * 1e6
    return f"{us / 1000:.2f} ms" if us >= 1000 else f"{us:.1f} µs"


def report(
    rows: list[Row],
    threshold: float,
    runs: int,
    base_label: str,
    exit_codes: dict[str, list[int]],
) -> str:
    pct = f"{threshold * 100:g}%"
    icons = {
        "ok": "✅",
        "slower": f"❌ more than {pct} slower",
        "failed": "⚠️ failed on PR",
        "new": "🆕 no baseline",
    }
    lines = [
        f"Time per operation; median of {runs} alternating runs per side.",
        f"Base: `{base_label}`. Fails when the PR is more than {pct} slower.",
        "",
        "| Benchmark | Base | PR | Change | Result |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        change = "–" if row.change is None else f"{row.change * 100:+.1f}%"
        lines.append(
            f"| `{row.name}` | {fmt_time(row.base)} | {fmt_time(row.pr)} "
            f"| {change} | {icons[row.status]} |"
        )
    for side in SIDES:
        bad = [(i, c) for i, c in enumerate(exit_codes[side]) if c != 0]
        for i, code in bad:
            lines.append("")
            lines.append(
                f"⚠️ {side} run {i + 1} exited with code {code}; check the job log."
            )
    return "\n".join(lines) + "\n"


def annotate(rows: list[Row], threshold: float):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for row in rows:
        if row.status == "slower":
            print(
                f"::error title=Benchmark regression::{row.name} is "
                f"{row.change * 100:.1f}% slower than base (limit {threshold * 100:g}%)"
            )
        elif row.status == "failed":
            print(f"::error title=Benchmark failed::{row.name} failed on the PR side")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base", default="origin/main", help="git ref to compare against"
    )
    parser.add_argument(
        "--out", type=Path, help="directory for results (default: temp)"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=int(os.environ.get("BENCH_RUNS") or DEFAULT_RUNS),
        help=f"runs per side (env BENCH_RUNS, default {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--threshold",
        default=os.environ.get("BENCH_FAIL_THRESHOLD") or DEFAULT_THRESHOLD,
        help=f"max allowed slowdown (env BENCH_FAIL_THRESHOLD, default {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--python", default="3.12", help="Python version for both sides"
    )
    args = parser.parse_args()
    threshold = parse_threshold(args.threshold)
    if args.runs < 1:
        sys.exit("--runs must be at least 1")

    repo = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True
        ).strip()
    )
    out = (args.out or Path(tempfile.mkdtemp(prefix="mlpa-bench-"))).resolve()
    results_dir = out / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    base_root = out / "base"
    roots = {"base": base_root, "pr": repo}
    venvs = {side: out / f"venv-{side}" for side in SIDES}
    base_sha = subprocess.check_output(
        ["git", "rev-parse", "--short", args.base], cwd=repo, text=True
    ).strip()
    base_label = f"{args.base} ({base_sha})"

    run(
        ["git", "worktree", "add", "--detach", "--force", str(base_root), args.base],
        cwd=repo,
    )
    try:
        shutil.rmtree(base_root / BENCH_PATH, ignore_errors=True)
        shutil.copytree(
            repo / BENCH_PATH,
            base_root / BENCH_PATH,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        requirements = bench_requirements(repo)
        for side in SIDES:
            prepare_side(roots[side], venvs[side], args.python, requirements)

        results: dict[str, list[dict[str, float]]] = {side: [] for side in SIDES}
        exit_codes: dict[str, list[int]] = {side: [] for side in SIDES}
        for i in range(args.runs):
            # Alternate which side goes first so drift on the runner hits both.
            order = SIDES if i % 2 == 0 else SIDES[::-1]
            for side in order:
                path = results_dir / f"{side}-{i + 1}.json"
                code = run_benches(roots[side], venvs[side], path, side == "base")
                exit_codes[side].append(code)
                results[side].append(per_op_medians(path))
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(base_root)], cwd=repo
        )

    rows = compare(results, threshold)
    text = report(rows, threshold, args.runs, base_label, exit_codes)
    (out / "report.md").write_text(text)
    print(f"\n{text}\nResults: {out}")
    annotate(rows, threshold)

    if not any(results["pr"]):
        print("No PR results were produced.", file=sys.stderr)
        return 1
    if any(code != 0 for code in exit_codes["pr"]):
        return 1
    return 1 if any(row.status in ("slower", "failed") for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())

"""
Turns a pytest JUnit XML report into a GitHub Actions job summary that shows,
for each failed test, the docstring explaining what it actually checks --
so a CI failure reads as "this contract broke" instead of a bare assertion
dump you have to go open logs to interpret.

Usage: uv run python scripts/ci_failure_summary.py <junit.xml> <banner>
Writes to $GITHUB_STEP_SUMMARY if set, otherwise stdout.
"""

import ast
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _find_docstring(
    file_path: str, class_name: str | None, func_name: str
) -> str | None:
    try:
        tree = ast.parse(Path(file_path).read_text())
    except (OSError, SyntaxError):
        return None

    scope = tree
    if class_name:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                scope = node
                break
        else:
            return None

    for node in ast.walk(scope):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        ):
            return ast.get_docstring(node)
    return None


def _split_classname(classname: str) -> tuple[list[str], str | None]:
    parts = classname.split(".")
    if parts and parts[-1][:1].isupper():
        return parts[:-1], parts[-1]
    return parts, None


def main(junit_path: str, banner: str) -> None:
    lines: list[str] = []
    if not Path(junit_path).exists():
        # A step before pytest (e.g. bringing up the docker-compose stack)
        # failed, so pytest never ran and never wrote this file. That's a
        # real failure -- just not one with test docstrings to show.
        lines.append(
            f"Tests never ran -- {junit_path!r} was not created. "
            "Check the earlier steps in this job for the actual failure."
        )
        _write(lines)
        return

    tree = ET.parse(junit_path)
    failures = []
    for testcase in tree.getroot().iter("testcase"):
        outcome = testcase.find("failure")
        if outcome is None:
            outcome = testcase.find("error")
        if outcome is None:
            continue

        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        module_parts, class_name = _split_classname(classname)
        file_path = testcase.get("file") or (Path(*module_parts).as_posix() + ".py")

        docstring = _find_docstring(file_path, class_name, name)
        message = (outcome.get("message") or "").strip().splitlines()
        failures.append(
            (f"{classname}::{name}", docstring, message[0] if message else "")
        )

    if not failures:
        lines.append("All tests passed.")
    else:
        if banner:
            lines.append(banner)
            lines.append("")
        lines.append("### Failed tests")
        lines.append("")
        for test_id, docstring, message in failures:
            lines.append(f"- **`{test_id}`**")
            if docstring:
                lines.append(
                    f"  - What this checks: {docstring.strip().splitlines()[0]}"
                )
            if message:
                lines.append(f"  - Failure: {message}")

    _write(lines)


def _write(lines: list[str]) -> None:
    output = "\n".join(lines) + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")

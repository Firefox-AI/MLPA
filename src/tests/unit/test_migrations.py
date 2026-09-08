"""AIPLAT-1189: rollback-app-attest-database.sh runs `alembic downgrade`, so a
migration that ships a stub `downgrade()` silently breaks the only rollback
path we have. Guard against that here instead of finding out during an
incident.
"""

import ast
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).parents[3] / "alembic" / "versions"


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.py"))


def _get_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _is_noop_body(body: list[ast.stmt]) -> bool:
    """True if a function body has no statement besides a docstring, `pass`,
    or `...` (Ellipsis) - i.e. nothing that could actually touch the DB.
    """
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            # docstring or bare `...` (both parse as ast.Constant)
            continue
        return False
    return True


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.stem)
def test_migration_has_real_downgrade(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    downgrade = _get_function(tree, "downgrade")
    assert downgrade is not None, f"{path.name} is missing a downgrade() function"
    assert not _is_noop_body(downgrade.body), (
        f"{path.name}'s downgrade() is a no-op (pass/... only). "
        "rollback-app-attest-database.sh runs `alembic downgrade` as the only "
        "rollback path (AIPLAT-1189) - a stub here means rollback silently "
        "does nothing. Write a real downgrade, or add an explicit "
        "`raise NotImplementedError(...)` if this migration truly can't be "
        "reversed."
    )

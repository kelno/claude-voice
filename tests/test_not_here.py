"""The list of upstream's tests that can't run here names only tests that exist."""

import ast
from pathlib import Path

from not_here import NOT_HERE

ROOT = Path(__file__).resolve().parent.parent


def test_every_test_the_fork_skips_still_exists_upstream():
    """A test upstream renamed or removed would otherwise stay listed, skipping nothing."""
    missing = []
    for node in NOT_HERE:
        file, name = node.split("::")
        tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
        if name not in defined:
            missing.append(node)
    assert missing == [], f"listed in tests/not_here.py but gone: {missing}"

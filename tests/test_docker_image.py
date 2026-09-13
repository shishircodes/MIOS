"""Every package the API imports must be in the container image.

`Dockerfile.api` copies source directories one at a time rather than the whole
tree, which keeps the image small and its contents deliberate. The cost is that
adding a package is two steps, and forgetting the second one produces the worst
shape of failure available:

* the build succeeds, because nothing imports anything at build time;
* the image is pushed and deployed;
* uvicorn fails on `import`, so the container never serves;
* the health check reports an unhealthy container, and the message that would
  explain it — `ModuleNotFoundError: No module named 'llm'` — is inside the
  container logs rather than in the deploy output.

The Dockerfile already carries two comments warning about exactly this. They
did not stop the `llm` package being left out, because a comment cannot fail a
build. This can.

Checked against imports rather than a list kept here: a second list would need
maintaining too, and would go stale the same way.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile.api"

#: Directories that are not first-party Python packages the API could import.
NOT_APP_CODE = {
    "web", "tests", "Documents", "demo", "storage", "data", "docs",
    "node_modules", "mios_poc.egg-info", "__pycache__", ".git", ".github",
    ".claude", ".venv", "venv",
}


def _first_party_packages() -> set[str]:
    """Top-level directories in the repo that are importable packages."""
    return {
        p.name for p in ROOT.iterdir()
        if p.is_dir() and p.name not in NOT_APP_CODE
        and not p.name.startswith(".")
        and (p / "__init__.py").exists()
    }


def _copied_into_image() -> set[str]:
    """Directories `Dockerfile.api` copies, from its COPY lines."""
    copied: set[str] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(?!--)(\S+)\s+\./?(\S*)", line)
        if not m:
            continue
        src = m.group(1)
        # `COPY data/synthetic_postings.jsonl ...` copies a file, not a package.
        copied.add(src.split("/")[0])
    return copied


def _imported_by(module_path: pathlib.Path, packages: set[str]) -> set[str]:
    """First-party top-level packages imported by one module."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                if head in packages:
                    found.add(head)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            head = node.module.split(".")[0]
            if head in packages:
                found.add(head)
    return found


def _reachable_from_api() -> set[str]:
    """Every first-party package reachable from the API, followed transitively.

    Transitively because the failure is an import chain: `api.server` imports
    `api.admin_api`, which imports `llm`. A check that only read the top of the
    chain would have passed while the container refused to start.
    """
    packages = _first_party_packages()
    seen: set[str] = {"api"}
    queue = [ROOT / "api"]

    while queue:
        pkg_dir = queue.pop()
        for module in pkg_dir.rglob("*.py"):
            if "__pycache__" in module.parts:
                continue
            for name in _imported_by(module, packages):
                if name not in seen:
                    seen.add(name)
                    queue.append(ROOT / name)
    return seen


def test_the_dockerfile_exists_where_this_test_expects():
    assert DOCKERFILE.exists(), "Dockerfile.api moved; this guard needs updating"


def test_every_package_the_api_imports_is_copied_into_the_image():
    """The check that would have caught `llm` before it reached a deployment."""
    needed = _reachable_from_api()
    copied = _copied_into_image()
    missing = sorted(needed - copied)

    assert not missing, (
        f"Dockerfile.api does not copy {missing}, but the API imports it.\n"
        "The image would build and push, then fail on `import` when uvicorn "
        "starts — which surfaces as an unhealthy container rather than as "
        "anything naming the missing module.\n"
        f"Add: {' '.join(f'COPY {m} ./{m}' for m in missing)}"
    )


def test_the_check_can_actually_fail():
    """A guard nobody has seen fail is a guard nobody should trust.

    Reads the Dockerfile with one package removed and confirms that is noticed.
    """
    needed = _reachable_from_api()
    assert needed, "found no packages at all — the import walk is broken"

    pretend_copied = _copied_into_image() - {"loader"}
    assert "loader" in needed - pretend_copied


@pytest.mark.parametrize("package", ["api", "loader", "llm", "push", "publish"])
def test_known_packages_are_present(package):
    """Names it in the failure message, so a red build says which one."""
    assert package in _copied_into_image(), f"Dockerfile.api does not copy {package}/"

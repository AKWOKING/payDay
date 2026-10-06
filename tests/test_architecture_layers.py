"""Architecture fitness functions — the layer rules, enforced instead of narrated.

`docs/design/SYSTEM_ARCHITECTURE.md` states who may talk to whom. Prose in a
design document does not survive a deadline; these tests do. They parse the
source with `ast` (not regex) and fail on the first import that crosses a
boundary in the wrong direction.

The rules
---------
* **adapters** are leaf integrations: they may not import services or the API.
* **services** own the business logic and may not import the API layer.
* **core / models** are the bottom of the stack: no imports from services,
  adapters, or the API.
* **schemas** are transport contracts: no imports from services, adapters, or
  the API.
* **api** may import services, schemas, core and models — but reaches operators
  only *through* services and the adapter factory, not by importing an adapter
  directly.

One documented exception exists, and it is asserted below so it cannot silently
spread: `api/v1/webhooks.py` is the provider protocol boundary — it parses the
operator's callback payload (`ProviderCallback`), so it necessarily speaks the
adapter's vocabulary.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "payday"

# Layer -> module prefixes it must never import.
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "adapters":     ("payday.services", "payday.api"),
    "services":     ("payday.api",),
    "models":       ("payday.services", "payday.adapters", "payday.api"),
    "core":         ("payday.services", "payday.adapters", "payday.api"),
    "schemas":      ("payday.services", "payday.adapters", "payday.api"),
    "api":          ("payday.adapters",),
}

# path (relative to src/payday) -> why this file is allowed to break its layer rule
DOCUMENTED_EXCEPTIONS = {
    "api/v1/webhooks.py": (
        "Provider protocol boundary: verifies operator callbacks and needs the "
        "ProviderCallback type plus the adapter factory to re-query status."
    ),
}


def _python_files(layer: str) -> list[Path]:
    return sorted(p for p in (SRC / layer).rglob("*.py") if p.name != "__init__.py")


def _imported_modules(path: Path) -> list[tuple[str, int]]:
    """Every `payday.*` module imported by a file, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("payday"):
                    found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("payday"):
                found.append((module, node.lineno))
    return found


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_IMPORTS))
def test_layer_does_not_import_upward(layer: str) -> None:
    banned = FORBIDDEN_IMPORTS[layer]
    violations: list[str] = []

    for path in _python_files(layer):
        relative = path.relative_to(SRC).as_posix()
        if relative in DOCUMENTED_EXCEPTIONS:
            continue
        for module, lineno in _imported_modules(path):
            if module.startswith(banned):
                violations.append(f"  {relative}:{lineno} imports {module}")

    assert not violations, (
        f"Layer '{layer}' must not import {banned}:\n" + "\n".join(violations)
    )


def test_documented_exceptions_still_exist_and_are_still_needed() -> None:
    """An exception that is no longer used must be deleted, not left as precedent.

    Exceptions rot into permissions. If the webhook controller stops importing
    adapters, this test fails and asks for the exception to be removed.
    """
    for relative in DOCUMENTED_EXCEPTIONS:
        path = SRC / relative
        assert path.exists(), f"Documented exception {relative} no longer exists"
        imports = [m for m, _ in _imported_modules(path)]
        assert any(m.startswith("payday.adapters") for m in imports), (
            f"{relative} no longer imports payday.adapters — remove it from "
            f"DOCUMENTED_EXCEPTIONS so the rule applies again"
        )


def test_no_module_in_the_wrong_place_imports_redis_directly() -> None:
    """Redis is reached through `core/redis_client`, never ad hoc.

    A second client would open its own pool and bypass the fail-closed startup
    check (WS-0) — the exact class of bug that rule exists to prevent.
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative in {"core/redis_client.py", "core/counters.py", "core/ratelimit.py"}:
            continue
        for module, lineno in _imported_modules(path):
            if module == "redis" or module.startswith("redis."):
                offenders.append(f"  {relative}:{lineno} imports {module}")

    assert not offenders, (
        "These modules import the redis client directly instead of going through "
        "payday.core.redis_client:\n" + "\n".join(offenders)
    )

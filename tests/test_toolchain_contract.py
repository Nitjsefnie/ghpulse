"""Contracts for the pinned development toolchain."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"
MIN_RUFF = (0, 16, 3)
PINNED_REQUIREMENT = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)=="
    r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
)


def test_dev_requirements_pin_ruff_to_a_supported_minimum():
    """CI's Ruff configuration requires the migrated rule-set behavior."""
    active_lines = [
        line.strip()
        for line in DEV_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    invalid_lines = [line for line in active_lines if not PINNED_REQUIREMENT.fullmatch(line)]
    assert not invalid_lines, (
        "requirements-dev.txt has invalid active lines; expected NAME==X.Y.Z: "
        f"{invalid_lines!r}"
    )

    parsed = []
    for line in active_lines:
        match = PINNED_REQUIREMENT.fullmatch(line)
        assert match, f"requirements-dev.txt entry did not parse: {line!r}"
        parsed.append(
            (
                match.group("name").lower(),
                tuple(int(match.group(part)) for part in ("major", "minor", "patch")),
                line,
            )
        )

    ruff_entries = [entry for entry in parsed if entry[0] == "ruff"]
    assert len(ruff_entries) == 1, (
        "requirements-dev.txt must contain exactly one Ruff requirement; "
        f"found {[entry[2] for entry in ruff_entries]!r}"
    )

    found = ruff_entries[0][1]
    assert found >= MIN_RUFF, (
        f"requirements-dev.txt has Ruff {found}; required minimum is {MIN_RUFF}"
    )

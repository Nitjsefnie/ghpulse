"""The deliberately small gh-widgets acquisition boundary for ghpulse.

The renderer project remains runnable on its own.  ghpulse loads its public
data module from the checked-out submodule instead of importing whichever
``ghwidgets_data`` happens to be installed in the interpreter.
"""

import importlib.util as _importlib_util
import os as _os
from pathlib import Path as _Path
import sys as _sys
from types import ModuleType as _ModuleType


__all__ = ["fetch_snapshot", "load_snapshot_file"]

_EXPECTED_SCHEMA_VERSION = 1
_REPOSITORY_ROOT = _Path(__file__).resolve().parents[1]
_VENDOR_ROOT = _REPOSITORY_ROOT / "vendor" / "gh-widgets"
_DATA_PATH = _VENDOR_ROOT / "ghwidgets_data.py"
_COMMON_PATH = _VENDOR_ROOT / "ghwidgets_common.py"


def _load_vendor_module() -> _ModuleType:
    """Load the pinned data module and its sibling dependency by exact path."""
    if not _DATA_PATH.is_file() or not _COMMON_PATH.is_file():
        raise ImportError(
            "gh-widgets submodule is not initialized at "
            f"{_VENDOR_ROOT}"
        )

    common_spec = _importlib_util.spec_from_file_location(
        "ghwidgets_common", _COMMON_PATH
    )
    if common_spec is None or common_spec.loader is None:
        raise ImportError(f"could not load {_COMMON_PATH}")
    common_module = _importlib_util.module_from_spec(common_spec)
    previous_common = _sys.modules.get("ghwidgets_common")
    _sys.modules["ghwidgets_common"] = common_module
    try:
        common_spec.loader.exec_module(common_module)
        data_spec = _importlib_util.spec_from_file_location(
            "_ghpulse_vendor_ghwidgets_data", _DATA_PATH
        )
        if data_spec is None or data_spec.loader is None:
            raise ImportError(f"could not load {_DATA_PATH}")
        data_module = _importlib_util.module_from_spec(data_spec)
        _sys.modules[data_spec.name] = data_module
        data_spec.loader.exec_module(data_module)
        return data_module
    finally:
        if previous_common is None:
            _sys.modules.pop("ghwidgets_common", None)
        else:
            _sys.modules["ghwidgets_common"] = previous_common


_ghwidgets_data = _load_vendor_module()
if getattr(_ghwidgets_data, "SCHEMA_VERSION", None) != _EXPECTED_SCHEMA_VERSION:
    raise ImportError(
        "unsupported gh-widgets snapshot schema version: "
        f"{getattr(_ghwidgets_data, 'SCHEMA_VERSION', None)!r}; "
        f"expected {_EXPECTED_SCHEMA_VERSION}"
    )
for _required in ("fetch_authored_snapshot", "load_snapshot"):
    if not callable(getattr(_ghwidgets_data, _required, None)):
        raise ImportError(f"gh-widgets data module lacks {_required}()")


def _validate_public_boundary(snapshot: object) -> dict:
    """Re-check the public boundary after every vendor call.

    The pinned producer already performs this validation.  Keeping this guard
    in the consumer prevents an accidental vendor update or a custom transport
    from allowing private data into the dashboard before the pin is reviewed.
    The vendor's public fetch/load functions handle the complete snapshot
    contract; this function adds consumer-owned visibility assertions.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("gh-widgets returned a snapshot object of the wrong type")
    validated = snapshot
    if validated.get("schema_version") != _EXPECTED_SCHEMA_VERSION:
        raise ValueError("snapshot has an unsupported schema_version")

    account = validated.get("account")
    if not isinstance(account, dict) or not isinstance(account.get("login"), str):
        raise ValueError("snapshot account is malformed")
    account_login = account["login"].casefold()
    repositories = validated.get("repositories")
    if not isinstance(repositories, list):
        raise ValueError("snapshot repositories must be a list")
    for index, repository in enumerate(repositories):
        if not isinstance(repository, dict) or repository.get("isPrivate") is not False:
            raise ValueError(
                f"snapshot repositories[{index}] must be public external data"
            )
        owner = repository.get("owner")
        if not isinstance(owner, dict) or not isinstance(owner.get("login"), str):
            raise ValueError(f"snapshot repositories[{index}] has a malformed owner")
        if owner["login"].casefold() == account_login:
            raise ValueError(
                f"snapshot repositories[{index}] must be external to the account"
            )

    for collection in ("issues", "pull_requests"):
        records = validated.get(collection)
        if not isinstance(records, list):
            raise ValueError(f"snapshot {collection} must be a list")
        for index, record in enumerate(records):
            if not isinstance(record, dict) or record.get("is_private") is not False:
                raise ValueError(
                    f"snapshot {collection}[{index}] must be public external data"
                )
    return validated


def fetch_snapshot(token: str, login: str) -> dict:
    """Fetch and validate one public, normalized snapshot for ``login``."""
    fixture = _os.environ.get("GHPULSE_TEST_SOURCE_SNAPSHOT", "").strip()
    if fixture:
        # The final integration test exercises the same validated source
        # boundary without depending on a live GitHub account. This explicit
        # test-only switch is never enabled by the production environment.
        return load_snapshot_file(fixture)
    return _validate_public_boundary(
        _ghwidgets_data.fetch_authored_snapshot(token, login)
    )


def load_snapshot_file(path: str | _Path) -> dict:
    """Load and validate a public, normalized snapshot JSON file."""
    return _validate_public_boundary(_ghwidgets_data.load_snapshot(path))

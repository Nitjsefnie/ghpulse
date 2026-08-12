"""Contract tests for the narrow gh-widgets source adapter."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend import github_source


FIXTURE = Path(__file__).parent / "fixtures" / "github_snapshot.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_load_snapshot_file_returns_valid_public_snapshot():
    body = github_source.load_snapshot_file(FIXTURE)

    assert body["schema_version"] == 1
    assert body["account"] == {"login": "octocat"}
    assert body["repositories"][0]["isPrivate"] is False
    assert all(not row["is_private"] for row in body["issues"])
    assert all(not row["is_private"] for row in body["pull_requests"])


def test_adapter_exports_only_the_two_source_functions():
    assert github_source.__all__ == ["fetch_snapshot", "load_snapshot_file"]


def test_load_snapshot_file_rejects_unsupported_schema_version(tmp_path):
    body = _fixture()
    body["schema_version"] = 999
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        github_source.load_snapshot_file(path)


@pytest.mark.parametrize("collection", ["repositories", "issues", "pull_requests"])
def test_load_snapshot_file_rejects_private_records(tmp_path, collection):
    body = _fixture()
    if collection == "repositories":
        body[collection][0]["isPrivate"] = True
    else:
        body[collection][0]["is_private"] = True
    path = tmp_path / f"private-{collection}.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match="public"):
        github_source.load_snapshot_file(path)


def test_fetch_snapshot_delegates_credentials_and_revalidates_result(monkeypatch):
    expected = _fixture()
    calls = []

    def fake_fetch(token, login):
        calls.append((token, login))
        return copy.deepcopy(expected)

    monkeypatch.setattr(github_source._ghwidgets_data, "fetch_authored_snapshot", fake_fetch)

    assert github_source.fetch_snapshot("secret-token", "octocat") == expected
    assert calls == [("secret-token", "octocat")]


def test_fetch_snapshot_rejects_private_upstream_result(monkeypatch):
    private = _fixture()
    private["issues"][0]["is_private"] = True
    monkeypatch.setattr(
        github_source._ghwidgets_data,
        "fetch_authored_snapshot",
        lambda token, login: private,
    )

    with pytest.raises(ValueError, match="public"):
        github_source.fetch_snapshot("secret-token", "octocat")

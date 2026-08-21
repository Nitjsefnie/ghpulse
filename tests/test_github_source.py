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


@pytest.mark.parametrize("mutation", ["unknown", "missing"])
def test_load_snapshot_file_rejects_unknown_or_missing_nested_fields(
    tmp_path, mutation
):
    body = _fixture()
    if mutation == "unknown":
        body["issues"][0]["unexpected"] = "not-part-of-v1"
    else:
        body["issues"][0].pop("url")
    path = tmp_path / f"malformed-{mutation}.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match=r"(unknown|missing|required|field)"):
        github_source.load_snapshot_file(path)


def test_load_snapshot_file_rejects_account_owned_repository(tmp_path):
    body = _fixture()
    body["repositories"][0]["owner"]["login"] = "octocat"
    body["repositories"][0]["nameWithOwner"] = "octocat/project"
    path = tmp_path / "account-owned.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ValueError, match=r"(owned|external|public)"):
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


def test_fetch_snapshot_delegates_credentials_and_checks_boundary(monkeypatch):
    expected = _fixture()
    calls = []

    def fake_fetch(token, login):
        calls.append((token, login))
        return copy.deepcopy(expected)

    monkeypatch.setattr(github_source._ghwidgets_data, "fetch_authored_snapshot", fake_fetch)

    assert github_source.fetch_snapshot("secret-token", "octocat") == expected
    assert calls == [("secret-token", "octocat")]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("private_repository", "public"),
        ("private_issue", "public"),
        ("account_owned", "external"),
        ("missing_account", "malformed"),
        ("missing_repository_owner", "malformed owner"),
        ("missing_issue_visibility", "public"),
        ("unsupported_schema", "schema_version"),
    ],
)
def test_fetch_snapshot_rejects_faulty_producer_boundary(
    monkeypatch, mutation, message
):
    invalid = _fixture()
    if mutation == "private_repository":
        invalid["repositories"][0]["isPrivate"] = True
    elif mutation == "private_issue":
        invalid["issues"][0]["is_private"] = True
    elif mutation == "account_owned":
        invalid["repositories"][0]["owner"]["login"] = "octocat"
    elif mutation == "missing_account":
        invalid.pop("account")
    elif mutation == "missing_repository_owner":
        invalid["repositories"][0].pop("owner")
    elif mutation == "missing_issue_visibility":
        invalid["issues"][0].pop("is_private")
    elif mutation == "unsupported_schema":
        invalid["schema_version"] = 999
    else:  # pragma: no cover - protects the test if a case is mistyped
        raise AssertionError(mutation)
    monkeypatch.setattr(
        github_source._ghwidgets_data,
        "fetch_authored_snapshot",
        lambda token, login: invalid,
    )

    with pytest.raises(ValueError, match=message):
        github_source.fetch_snapshot("secret-token", "octocat")

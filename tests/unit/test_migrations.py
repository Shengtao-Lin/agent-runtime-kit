"""Public migration API, explicit CLI targets, and credential-safe errors."""

from __future__ import annotations

import sys
from importlib.resources import files

import pytest

from agent_runtime.migrations import __main__ as cli
from agent_runtime.migrations import heads, upgrade


def test_packaged_revision_and_template_are_available(capsys: pytest.CaptureFixture[str]) -> None:
    resources = files("agent_runtime").joinpath("migrations")
    assert resources.joinpath("env.py").is_file()
    assert resources.joinpath("script.py.mako").is_file()
    assert resources.joinpath("versions", "0001_initial_schema.py").is_file()
    heads()
    assert "0002 (head)" in capsys.readouterr().out


def test_offline_upgrade_preserves_percent_encoded_url(capsys: pytest.CaptureFixture[str]) -> None:
    upgrade("postgresql+psycopg://user:percent%25encoded@invalid.example/database", sql=True)
    output = capsys.readouterr().out
    assert "CREATE TABLE runtime_runs" in output
    assert "CREATE TABLE feedback_records" in output
    assert "0001" in output
    assert "0002" in output
    assert "legacy_owner_unavailable" in output
    assert "uq_runtime_runs_active_thread" in output
    assert "percent%25encoded" not in output


def test_cli_requires_explicit_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent-runtime-migrate", "upgrade"])
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 2
    assert "DATABASE_URL is required" in capsys.readouterr().err


def test_cli_heads_does_not_require_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["agent-runtime-migrate", "heads"])
    cli.main()
    assert "0002 (head)" in capsys.readouterr().out


def test_cli_hides_database_exception_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("synthetic-password-sentinel")

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@invalid.example/database")
    monkeypatch.setattr(sys, "argv", ["agent-runtime-migrate", "upgrade"])
    monkeypatch.setattr(cli, "upgrade", fail)
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 1
    error = capsys.readouterr().err
    assert "Migration failed" in error
    assert "sentinel" not in error
    assert "secret" not in error

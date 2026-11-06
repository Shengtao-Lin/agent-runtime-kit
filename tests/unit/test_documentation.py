"""Navigation guards detect broken acceptance evidence and ADR identities."""

from pathlib import Path

from scripts.check_docs import anchors, document_errors


def test_repository_documentation_is_navigable() -> None:
    assert not document_errors(Path(__file__).parents[2])


def test_links_and_anchors_reject_missing_evidence(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "[evidence](docs/validation/missing.md)\n[anchor](guide.md#absent)\n"
        "[outside](../outside.md)\n[reference][proof]\n\n[proof]: lost.md\n",
        encoding="utf-8",
    )
    (tmp_path / "guide.md").write_text("# Present\n", encoding="utf-8")
    errors = document_errors(tmp_path)
    assert len(errors) == 4
    assert any("missing.md" in error for error in errors)
    assert any("missing heading anchor" in error for error in errors)
    assert any("escapes repository" in error for error in errors)
    assert any("lost.md" in error for error in errors)


def test_valid_links_ignore_code_and_remote_destinations(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Same `Title`!\n# Same Title\n[repeat](#same-title-1)\n"
        '[file](<space name.md#hello-world> "title")\n'
        "[remote](https://example.invalid/missing)\n"
        "`[sample](missing.md)`\n~~~~md\n[sample](missing.md)\n~~~~\n",
        encoding="utf-8",
    )
    (tmp_path / "space name.md").write_text("# Hello World\n", encoding="utf-8")
    assert not document_errors(tmp_path)
    assert anchors("# A\n# A\n# A-1\n") == {"a", "a-1", "a-1-1"}


def test_duplicate_or_unindexed_canonical_adr_fails(tmp_path: Path) -> None:
    folder = tmp_path / "docs" / "adr"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("[first](0001-first.md)\n", encoding="utf-8")
    for name in ("0001-first.md", "0001-second.md"):
        (folder / name).write_text("# ADR 0001: Decision\n", encoding="utf-8")
    errors = document_errors(tmp_path)
    assert any("duplicate canonical" in error for error in errors)
    assert any("must appear once" in error for error in errors)


def test_historical_alias_cannot_hide_a_second_decision(tmp_path: Path) -> None:
    folder = tmp_path / "docs" / "adr"
    folder.mkdir(parents=True)
    (folder / "0010-managed-runtime-lifetime.md").write_text(
        "# ADR 0010: Hidden decision\n", encoding="utf-8"
    )
    assert any("invalid historical ADR alias" in error for error in document_errors(tmp_path))

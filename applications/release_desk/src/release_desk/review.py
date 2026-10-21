"""Read-only GitHub release metadata and an application-owned checklist."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)
    tag: str = Field(pattern=r"^v\d+\.\d+\.\d+$", max_length=64)
    distribution: str = Field(
        default="agent-runtime-kit", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=100
    )

    @field_validator("repository")
    @classmethod
    def valid_segments(cls, value: str) -> str:
        if any(segment in {".", ".."} for segment in value.split("/")):
            raise ValueError("Repository segments must be names")
        return value


class Asset(BaseModel):
    name: str
    size: int = Field(ge=0)
    digest: str | None = None


class Release(BaseModel):
    tag_name: str
    draft: bool
    prerelease: bool
    assets: list[Asset] = Field(max_length=1000)


class ReleaseSource(Protocol):
    async def fetch(self, request: ReviewInput) -> Release: ...


class GitHubReleaseSource:
    """Fetch only public metadata, with no credentials and no artifact execution."""

    async def fetch(self, request: ReviewInput) -> Release:
        def read() -> Release:
            url = f"https://api.github.com/repos/{request.repository}/releases/tags/{request.tag}"
            query = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "runtime-release-desk/0.1",
                },
            )
            try:
                with urllib.request.urlopen(query, timeout=10) as response:
                    content = response.read(1_000_001)
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeError(
                    "Public release metadata is unavailable; check tag or API limits"
                ) from exc
            if len(content) > 1_000_000:
                raise ValueError("Release metadata exceeds the application limit")
            return Release.model_validate(json.loads(content))

        return await asyncio.to_thread(read)


class Check(BaseModel):
    name: str
    passed: bool


class ReviewReport(BaseModel):
    repository: str
    tag: str
    metadata_ready: bool
    checks: list[Check]
    boundary: str = (
        "Metadata checks only; no artifact installation, CI certification or security audit."
    )


async def review_release(source: ReleaseSource, request: ReviewInput) -> ReviewReport:
    release = await source.fetch(request)
    name = request.distribution.replace("-", "_")
    version = request.tag[1:]
    expected = [f"{name}-{version}-py3-none-any.whl", f"{name}-{version}.tar.gz"]
    checks = [
        Check(name="tag_matches", passed=release.tag_name == request.tag),
        Check(name="published_stable", passed=not release.draft and not release.prerelease),
    ]
    for filename in expected:
        matches = [asset for asset in release.assets if asset.name == filename]
        checks.append(
            Check(name=f"artifact:{filename}", passed=len(matches) == 1 and matches[0].size > 0)
        )
        digest = matches[0].digest if len(matches) == 1 else None
        valid_digest = False
        if digest and digest.startswith("sha256:") and len(digest) == 71:
            valid_digest = all(char in "0123456789abcdefABCDEF" for char in digest[7:])
        checks.append(Check(name=f"sha256_recorded:{filename}", passed=valid_digest))
    return ReviewReport(
        repository=request.repository,
        tag=request.tag,
        metadata_ready=all(check.passed for check in checks),
        checks=checks,
    )

#!/usr/bin/env python3
"""Bounded discovery of Git working trees for Telegram agent launches."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time


DEFAULT_PRUNED_DIRECTORIES = {
    ".cache",
    ".cargo",
    ".npm",
    ".rustup",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}
MAX_SCANNED_DIRECTORIES = 50_000
MAX_DISCOVERED_REPOSITORIES = 5_000


class RepositoryCatalogError(RuntimeError):
    """Repository discovery could not complete within its safety boundary."""


@dataclass(frozen=True)
class Repository:
    path: Path
    relative_path: str

    @property
    def name(self) -> str:
        return self.path.name or self.relative_path


class RepositoryCatalog:
    def __init__(
        self,
        root: Path,
        *,
        cache_seconds: int = 60,
        pruned_directories: set[str] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.cache_seconds = max(0, cache_seconds)
        self.pruned_directories = set(
            DEFAULT_PRUNED_DIRECTORIES
            if pruned_directories is None
            else pruned_directories
        )
        self._cached_at = 0.0
        self._cached: list[Repository] = []
        self._cache_populated = False

    def _inside_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_git_working_tree(path: Path) -> bool:
        marker = path / ".git"
        return marker.is_dir() or marker.is_file()

    def validate(self, path: Path) -> Path:
        try:
            candidate = Path(path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise RepositoryCatalogError(
                "The selected repository path could not be resolved"
            ) from exc
        if (
            not self.root.is_dir()
            or not candidate.is_dir()
            or not self._inside_root(candidate)
            or not self._is_git_working_tree(candidate)
        ):
            raise RepositoryCatalogError(
                "The selected repository is no longer available under the configured root"
            )
        return candidate

    def validate_directory(self, path: Path) -> Path:
        """Validate a trusted conversation cwd beneath the configured root."""
        try:
            candidate = Path(path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise RepositoryCatalogError(
                "The conversation directory could not be resolved"
            ) from exc
        if (
            not self.root.is_dir()
            or not candidate.is_dir()
            or not self._inside_root(candidate)
        ):
            raise RepositoryCatalogError(
                "The conversation directory is outside the configured repository root"
            )
        return candidate

    def discover(self, *, refresh: bool = False) -> list[Repository]:
        now = time.monotonic()
        if (
            not refresh
            and self._cache_populated
            and now - self._cached_at <= self.cache_seconds
        ):
            return list(self._cached)
        if not self.root.is_dir():
            raise RepositoryCatalogError(
                f"Repository root is not a directory: {self.root}"
            )

        repositories: dict[Path, Repository] = {}
        scanned = 0
        for current_raw, directories, _files in os.walk(
            self.root, topdown=True, followlinks=False
        ):
            scanned += 1
            if scanned > MAX_SCANNED_DIRECTORIES:
                raise RepositoryCatalogError(
                    "Repository scan exceeded its directory safety limit"
                )

            current = Path(current_raw)
            is_repository = self._is_git_working_tree(current)
            directories[:] = sorted(
                directory
                for directory in directories
                if directory != ".git"
                and directory not in self.pruned_directories
                and not (current / directory).is_symlink()
            )
            if not is_repository:
                continue

            try:
                resolved = current.resolve()
            except (OSError, RuntimeError):
                continue
            if (
                not self._inside_root(resolved)
                or not self._is_git_working_tree(resolved)
            ):
                continue
            relative = resolved.relative_to(self.root)
            label = self.root.name if str(relative) == "." else str(relative)
            repositories[resolved] = Repository(resolved, label)
            if len(repositories) > MAX_DISCOVERED_REPOSITORIES:
                raise RepositoryCatalogError(
                    "Repository scan exceeded its repository safety limit"
                )

        discovered = sorted(
            repositories.values(), key=lambda repository: repository.relative_path.casefold()
        )
        self._cached = discovered
        self._cached_at = now
        self._cache_populated = True
        return list(discovered)

    def search(
        self, query: str, *, limit: int = 8
    ) -> tuple[list[Repository], int]:
        normalized = " ".join(str(query or "").casefold().split())
        terms = normalized.split()
        matches = []
        for repository in self.discover():
            haystack = repository.relative_path.casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            matches.append(repository)

        def rank(repository: Repository) -> tuple[int, int, str]:
            name = repository.name.casefold()
            relative = repository.relative_path.casefold()
            if not normalized:
                category = 3
            elif name == normalized:
                category = 0
            elif name.startswith(normalized):
                category = 1
            elif normalized in name:
                category = 2
            else:
                category = 3
            return category, len(relative), relative

        matches.sort(key=rank)
        return matches[: max(1, limit)], len(matches)

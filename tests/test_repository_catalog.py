from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "telegram/repositories.py"
SPEC = importlib.util.spec_from_file_location("muxpager_repositories", MODULE_PATH)
assert SPEC and SPEC.loader
repositories = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repositories
SPEC.loader.exec_module(repositories)


class RepositoryCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        (self.root / "Alpha" / ".git").mkdir(parents=True)
        (self.root / "clients" / "alpha-tools").mkdir(parents=True)
        (self.root / "clients" / "alpha-tools" / ".git").write_text(
            "gitdir: elsewhere\n", encoding="utf-8"
        )
        (self.root / "clients" / "Beta" / ".git").mkdir(parents=True)

    def test_discovers_git_working_trees_and_searches_relative_paths(self) -> None:
        catalog = repositories.RepositoryCatalog(self.root, cache_seconds=0)

        found = catalog.discover()
        matches, total = catalog.search("client alpha")

        self.assertEqual(
            ["Alpha", "clients/alpha-tools", "clients/Beta"],
            [repository.relative_path for repository in found],
        )
        self.assertEqual(1, total)
        self.assertEqual("clients/alpha-tools", matches[0].relative_path)

    def test_does_not_traverse_pruned_directories_or_symlinks(self) -> None:
        (self.root / ".cache" / "hidden" / ".git").mkdir(parents=True)
        outside = self.base / "outside"
        (outside / ".git").mkdir(parents=True)
        (self.root / "outside-link").symlink_to(outside, target_is_directory=True)
        catalog = repositories.RepositoryCatalog(self.root, cache_seconds=0)

        found = catalog.discover()

        paths = {repository.path for repository in found}
        self.assertNotIn((self.root / ".cache" / "hidden").resolve(), paths)
        self.assertNotIn(outside.resolve(), paths)

    def test_validate_rejects_non_repository_and_path_outside_root(self) -> None:
        catalog = repositories.RepositoryCatalog(self.root)
        outside = self.base / "outside"
        (outside / ".git").mkdir(parents=True)
        plain = self.root / "plain"
        plain.mkdir()

        with self.assertRaises(repositories.RepositoryCatalogError):
            catalog.validate(outside)
        with self.assertRaises(repositories.RepositoryCatalogError):
            catalog.validate(plain)


if __name__ == "__main__":
    unittest.main()

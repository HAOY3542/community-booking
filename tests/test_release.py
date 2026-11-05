import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import check_release as release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="booking-release-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "source"
        self.root.mkdir()
        self.files = ["RELEASE_ALLOWLIST.txt", "example.py"]
        (self.root / "RELEASE_ALLOWLIST.txt").write_text("\n".join(self.files) + "\n")
        (self.root / "example.py").write_text("# synthetic fixture\n")
        self.refresh()

    def refresh(self):
        (self.root / "SHA256SUMS").write_text(release.checksums(self.root, self.files))

    def categories(self):
        return {finding["category"] for finding in release.inspect(self.root)[1]}

    def test_clean_extracted_inventory_and_hashes(self):
        self.assertEqual(release.inspect(self.root), (self.files, []))

    def test_stale_hash_is_rejected(self):
        (self.root / "example.py").write_text("# changed after review\n")
        self.assertIn("checksum-mismatch", self.categories())

    def test_unlisted_source_is_rejected(self):
        (self.root / "accidental.txt").write_text("synthetic nonsecret fixture")
        self.assertIn("outside-allowlist", self.categories())

    def test_git_inventory_catches_forced_tracked_ignored_file(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text(".env*\n")
        (self.root / ".env.synthetic").write_text("SYNTHETIC=true\n")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", ".env.synthetic"], check=True)
        findings = release.inspect(self.root)[1]
        self.assertIn({"path": ".env.synthetic", "category": "outside-allowlist"}, findings)

    def test_new_untracked_source_is_checked_in_git(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "forgotten.py").write_text("# forgotten\n")
        self.assertIn("outside-allowlist", self.categories())

    def test_manifest_missing_duplicate_extra_or_invalid_entries(self):
        good = (self.root / "SHA256SUMS").read_text()
        for text in [good.splitlines()[0] + "\n", good + good.splitlines()[0] + "\n",
                     good + "0" * 64 + "  extra.py\n", "invalid\n"]:
            with self.subTest(text_length=len(text)):
                (self.root / "SHA256SUMS").write_text(text)
                self.assertTrue(self.categories())

    def test_missing_source_and_symlink_are_rejected(self):
        source = self.root / "example.py"
        source.unlink()
        self.assertIn("missing-or-symlink", self.categories())
        outside = self.root.parent / "outside.py"
        outside.write_text("# outside\n")
        source.symlink_to(outside)
        self.assertIn("missing-or-symlink", self.categories())

    def test_parent_symlink_is_rejected(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "file.py").write_text("# outside\n")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        self.files.append("linked/file.py")
        (self.root / "RELEASE_ALLOWLIST.txt").write_text("\n".join(self.files) + "\n")
        self.assertIn("missing-or-symlink", self.categories())

    def test_traversal_and_runtime_paths_are_rejected(self):
        for name in ["../outside.py", "runtime/data.txt", ".env.local", "./example.py", "/etc/passwd"]:
            with self.subTest(name=name):
                (self.root / "RELEASE_ALLOWLIST.txt").write_text(name + "\n")
                self.assertIn("forbidden-path", self.categories())

    def test_secret_heuristic_does_not_echo_value(self):
        value = "-----BEGIN " + "PRIVATE KEY-----"
        (self.root / "example.py").write_text(value)
        self.assertIn({"path": "example.py", "category": "private-key"}, release.inspect(self.root)[1])
        self.assertNotIn(value, str(release.inspect(self.root)[1]))

    def test_stage_verifies_copy_and_refuses_overwrite(self):
        target = self.root.parent / "staged"
        with patch.object(release, "ROOT", self.root), patch("sys.argv", ["check_release", "--stage", str(target)]), contextlib.redirect_stdout(io.StringIO()):
            release.main()
            self.assertEqual(release.inspect(target), (self.files, []))
            with self.assertRaises(ValueError):
                release.main()

    def test_relative_sibling_stage_allowed_but_nested_stage_refused(self):
        with patch.object(release, "ROOT", self.root), contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()):
            with patch("sys.argv", ["check_release", "--stage", "../relative-staged"]):
                release.main()
            self.assertEqual(release.inspect(self.root.parent / "relative-staged"), (self.files, []))
            with patch("sys.argv", ["check_release", "--stage", "nested"]), self.assertRaises(ValueError):
                release.main()


if __name__ == "__main__":
    unittest.main()

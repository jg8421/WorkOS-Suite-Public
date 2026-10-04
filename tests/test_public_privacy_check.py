"""Synthetic privacy gate fixtures; no original user records or accounts."""
from __future__ import annotations

from contextlib import redirect_stdout
import base64
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools.public_privacy_check import Limits, check_package, check_source, main


class PublicPrivacyCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, body):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
        return path

    def archive(self, members, name="release.zip"):
        path = self.base / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, body in members:
                archive.writestr(member, body)
        return path

    def categories(self, report):
        return {finding["category"] for finding in report["findings"]}

    def test_single_and_json_escaped_personal_paths_block_without_echoing_body(self):
        user = "ExampleOperator"
        plain = "C:" + "\\" + "Users" + "\\" + user + "\\notes.txt"
        self.write("plain.py", plain)
        self.write("escaped.json", json.dumps({"path": plain}))
        report = check_source(self.source)
        self.assertEqual(report["status"], "fail")
        self.assertEqual({item["file"] for item in report["findings"]}, {"plain.py", "escaped.json"})
        self.assertEqual(self.categories(report), {"personal_user_path"})
        self.assertNotIn(plain, json.dumps(report))
        self.assertEqual(report["files_scanned"], 2)

    def test_private_catalogue_is_not_confused_with_npm_private_package(self):
        self.write("repositories.json", json.dumps([
            {"full_name": "example-owner/internal-tool", "html_url": "https://github.com/example-owner/internal-tool", "private": True},
            {"full_name": "example-owner/public-tool", "private": False},
        ]))
        self.write("package.json", json.dumps({"name": "example-package", "private": True}))
        report = check_source(self.source)
        self.assertEqual(self.categories(report), {"private_catalogue_entry"})
        self.assertEqual(report["blocked_count"], 1)
        self.assertEqual(report["findings"][0]["file"], "repositories.json")

    def test_generic_organization_sync_path_blocks_without_a_private_name_list(self):
        organization = "Example Research Group"
        body = "C:" + "\\" + "shared" + "\\" + "OneDrive - " + organization + "\\notes.txt"
        self.write("provenance.json", json.dumps({"path": body}))
        self.write("public_description.md", "OneDrive-backed data folder")
        self.write("process.ps1", "Get-Process OneDrive -ErrorAction SilentlyContinue")
        report = check_source(self.source)
        self.assertIn("organization_sync_path", self.categories(report))
        self.assertEqual({item["file"] for item in report["findings"]}, {"provenance.json"})
        self.assertNotIn(organization, json.dumps(report))

    def test_high_confidence_credentials_in_owned_source_and_package_fail(self):
        token = "sk-" + "proj-" + "A1b2C3d4" * 7
        self.write("adapter.py", "KEY='" + token + "'")
        report = check_source(self.source)
        self.assertIn("api_credential", self.categories(report))
        self.assertEqual(report["status"], "fail")
        archive = self.archive([("Suite/app/adapter.py", "KEY='" + token + "'")])
        packaged = check_package(archive)
        self.assertEqual(packaged["status"], "fail")
        self.assertNotIn(token, json.dumps(report) + json.dumps(packaged))

    def test_github_bearer_and_pem_are_detected_but_header_only_is_not_a_key(self):
        github = "ghp" + "_" + "Ab19Cd23Ef45" * 4
        bearer = "Bearer " + "Ab19Cd23Ef45" * 4
        begin = "-----BEGIN " + "PRIVATE KEY-----"
        pem = begin + "\n" + "QUJD" * 16 + "\n-----END " + "PRIVATE KEY-----"
        self.write("transport.txt", github + "\n" + bearer + "\n" + pem)
        self.write("serializer.py", "HEADER='" + begin + "'")
        report = check_source(self.source)
        self.assertTrue({"github_credential", "bearer_credential", "private_key"}.issubset(self.categories(report)))
        self.assertFalse(any(item["file"] == "serializer.py" for item in report["findings"]))
        for value in (github, bearer, pem):
            self.assertNotIn(value, json.dumps(report))

    def test_obvious_fake_documentation_token_needs_review_and_is_not_silently_ignored(self):
        token = "sk-" + "synthetic-fixture-token-1234567890"
        self.write("docs/connection.md", "Example: " + token)
        report = check_source(self.source)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["blocked_count"], 0)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["findings"][0]["severity"], "needs_review")
        # A non-fixture literal cannot hide behind a documentation filename.
        self.write("docs/production.md", "sk-" + "proj-" + "Ab19Cd23Ef45" * 4)
        self.assertEqual(check_source(self.source)["status"], "fail")

    def test_valid_jwt_shape_in_a_test_file_is_not_blanket_exempted(self):
        def encoded(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        token = ".".join((encoded(b'{"alg":"HS256"}'), encoded(b'{"sub":"100"}'), encoded(bytes(range(32)))))
        self.write("tests/test_auth_fixture.txt", token)
        report = check_source(self.source)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(self.categories(report), {"jwt_credential"})
        self.assertEqual(report["review_count"], 0)

    def test_invalid_length_jwt_redaction_fixture_is_reviewed_not_hidden(self):
        def encoded(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        token = ".".join((encoded(b'{"alg":"HS256"}'), encoded(b'{"sub":"100"}'), encoded(bytes(range(9)))))
        self.write("tests/test_redaction_fixture.txt", token)
        report = check_source(self.source)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["findings"][0]["severity"], "needs_review")
        self.assertNotIn(token, json.dumps(report))

    def test_public_licenses_and_authors_are_preserved_not_treated_as_user_pii(self):
        license_body = "Copyright Example Author <author@example.org>\nMIT License\nhttps://github.com/public-author/library\n"
        self.write("components/vendor/LICENSE", license_body)
        self.write("provenance.json", json.dumps({"repository": "public-author/library", "private": False}))
        self.assertEqual(check_source(self.source)["findings"], [])
        path = self.archive([("Suite/runtime/library/LICENSE", license_body)])
        self.assertEqual(check_package(path)["findings"], [])

    def test_binary_utf16_personal_marker_in_apk_member_is_detected(self):
        marker = "C:" + "\\" + "Users" + "\\" + "ExampleOperator" + "\\notes.txt"
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            archive.writestr("classes.dex", b"\x7f\x00\x01" + marker.encode("utf-16le") + b"\x00\xff")
        package = self.archive([("Suite/app/component.apk", inner.getvalue())])
        report = check_package(package)
        self.assertEqual(report["status"], "fail")
        self.assertIn("personal_user_path", self.categories(report))
        self.assertTrue(any(item["file"].endswith("component.apk!classes.dex") for item in report["findings"]))
        self.assertNotIn(marker, json.dumps(report))

    def test_database_auth_recording_and_pairing_assets_are_rejected_without_opening(self):
        for name in ("auth.json", "records.sqlite3", "audio.wav", "phone-state.json", "adbkey.pub"):
            self.write(name, "NOT TO BE READ")
        with mock.patch.object(Path, "open", side_effect=AssertionError("Private asset was opened")):
            report = check_source(self.source)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["files_scanned"], 0)
        self.assertEqual(report["blocked_count"], 5)
        self.assertIn("phone_pairing_state", self.categories(report))

    def test_local_scratch_and_runtime_are_excluded_not_imported(self):
        marker = "C:" + "\\" + "Users" + "\\" + "ExampleOperator" + "\\notes.txt"
        self.write("work/local.txt", marker)
        self.write("runtime/local.txt", marker)
        self.write("node_modules/local.txt", marker)
        self.write("app.py", "print('Public application')")
        report = check_source(self.source)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["files_scanned"], 1)

    def test_symlink_source_file_is_refused_and_target_is_not_read(self):
        target = self.base / "outside"
        target.mkdir()
        (target / "private.txt").write_text("Do not read", encoding="utf-8")
        link = self.source / "linked-folder"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            import os
            if os.name != "nt":
                self.skipTest("OS account cannot create a symbolic link")
            # This junction is wholly within our temporary fixture; cleanup is
            # Python's native rmtree, which does not recurse into junctions.
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, "Could not create the owned junction fixture")
        report = check_source(self.source)
        self.assertEqual(self.categories(report), {"source_link_or_reparse"})
        self.assertEqual(report["files_scanned"], 0)

    def test_archive_absolute_drive_parent_and_link_entries_are_refused(self):
        for name in ("../outside.txt", "/outside.txt", "C:/outside.txt", "nested/../../outside.txt"):
            with self.subTest(name=name):
                path = self.archive([(name, "Do not extract")])
                self.assertIn("unsafe_archive_path", self.categories(check_package(path)))
        path = self.base / "linked.zip"
        member = zipfile.ZipInfo("Suite/app/linked.txt")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(member, "../../outside.txt")
        self.assertIn("archive_link_or_reparse", self.categories(check_package(path)))
        self.assertFalse((self.base / "outside.txt").exists())

    def test_decompression_member_ratio_total_entry_and_depth_limits_fail_closed(self):
        path = self.archive([("Suite/app/large.txt", "x" * 1000)])
        self.assertIn("member_size_limit", self.categories(check_package(path, limits=Limits(max_member_bytes=500, max_ratio=10_000))))
        self.assertIn("archive_ratio_limit", self.categories(check_package(path, limits=Limits(max_ratio=2))))
        path = self.archive([("a.txt", "abcdefghij"), ("b.txt", "klmnopqrst")])
        self.assertIn("archive_entry_limit", self.categories(check_package(path, limits=Limits(max_entries=1))))
        budget = self.archive([("a.txt", "x" * 300), ("b.txt", "y" * 300)], name="budget.zip")
        self.assertIn("expansion_budget", self.categories(check_package(budget, limits=Limits(max_total_bytes=500))))
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("inner.txt", "Public text")
        middle = io.BytesIO()
        with zipfile.ZipFile(middle, "w") as archive:
            archive.writestr("inner.zip", nested.getvalue())
        path = self.archive([("middle.zip", middle.getvalue())])
        self.assertIn("archive_depth_limit", self.categories(check_package(path, limits=Limits(max_depth=1))))

    def test_corrupt_archive_fails_closed_without_exception_or_raw_payload(self):
        path = self.base / "broken.zip"
        path.write_bytes(b"This is not a ZIP archive")
        report = check_package(path)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(self.categories(report), {"invalid_or_unreadable_archive"})
        self.assertNotIn("This is not", json.dumps(report))

    def test_package_checks_runtime_for_real_secrets_and_allows_public_code(self):
        token = "github" + "_pat_" + "Ab19Cd23Ef45" * 4
        path = self.archive([
            ("Suite/runtime/python/public_module.py", "import webbrowser"),
            ("Suite/runtime/node/config.txt", token),
        ])
        report = check_package(path)
        self.assertEqual(self.categories(report), {"github_credential"})
        self.assertEqual(report["status"], "fail")

    def test_cli_report_is_atomic_sanitized_and_the_only_output_file(self):
        token = "ghp" + "_" + "Ab19Cd23Ef45" * 4
        self.write("client.py", token)
        report_path = self.base / "reports" / "privacy.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = main(["--source", str(self.source), "--report", str(report_path)])
        self.assertEqual(status, 1)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["blocked_count"], 1)
        self.assertNotIn(token, stdout.getvalue() + report_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(p.name for p in report_path.parent.iterdir()), ["privacy.json"])
        self.assertEqual(sorted(p.name for p in self.source.iterdir()), ["client.py"])

    def test_invalid_input_returns_safe_error_code(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["--source", str(self.base / "missing")])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())["category"], "invalid_input_or_report")
        self.assertNotIn(str(self.base), output.getvalue())


if __name__ == "__main__":
    unittest.main()

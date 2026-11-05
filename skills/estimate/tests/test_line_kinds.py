"""The kind of a changed path (src, tests, docs, vendored, other): vendored folders, lockfiles and minified files first, then
documentation, then test code by folder or file name, then source code by extension; everything else is other."""
import unittest

from estimation.line_kinds import KINDS, RULES, kind_of


class LineKindsTest(unittest.TestCase):
    def assert_kinds(self, kind, paths):
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(kind, kind_of(path))

    def test_every_kind_has_a_rule_text(self):
        self.assertEqual(set(KINDS), set(RULES))
        self.assertTrue(all(RULES[kind] for kind in KINDS))

    def test_vendored_folders_lockfiles_and_minified_files(self):
        self.assert_kinds("vendored", ["web/node_modules/react/index.js", "Vendor/lib/a.go", "third_party/x/README.md",
                                       "Pods/Alamofire/Source.swift", ".venv/lib/site.py", "package-lock.json", "app/yarn.lock",
                                       "Cargo.lock", "Package.resolved", "go.sum", "assets/app.min.js", "css/site.MIN.CSS",
                                       "docs/vendor/guide.md"])

    def test_docs_by_extension_or_folder(self):
        self.assert_kinds("docs", ["README.md", "guide.MDX", "notes.rst", "book.adoc", "docs/diagram.png",
                                   "Documentation/api.json", "doc/tests/test_x.py"])

    def test_tests_by_folder_or_file_name(self):
        self.assert_kinds("tests", ["tests/test_a.py", "src/__tests__/a.tsx", "Spec/model.rb", "e2e/flow.ts",
                                    "MyAppTests/LoginTests.swift", "pkg/test_util.py", "pkg/util_test.py", "pkg/server_test.go",
                                    "web/a.test.ts", "web/a.spec.mjs", "src/FooTest.java", "src/FooTests.kt", "app/FooTests.cs",
                                    "lib/foo_spec.rb", "src/FooTest.php"])

    def test_tests_suffix_folder_and_camel_case_file_names_are_case_sensitive(self):
        self.assertEqual("src", kind_of("lib/mytests/a.swift"))
        self.assertEqual("src", kind_of("src/Latest.java"))
        self.assertEqual("src", kind_of("src/Contest.kt"))

    def test_test_folders_hold_only_code_as_tests(self):
        self.assert_kinds("other", ["tests/fixtures/data.json", "tests/snapshots/out.txt", "spec/support/config.yml"])

    def test_source_by_extension(self):
        self.assert_kinds("src", ["src/app.py", "web/App.TSX", "main.go", "lib.rs", "index.html", "style.scss", "schema.sql",
                                  "run.sh", "api.proto", "contest.py", "testing_tools.py"])

    def test_everything_else_is_other(self):
        self.assert_kinds("other", ["package.json", ".github/workflows/ci.yml", "config.toml", "logo.png", "Makefile",
                                    "Dockerfile", "data/seed.csv"])


if __name__ == "__main__":
    unittest.main()

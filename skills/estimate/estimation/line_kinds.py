"""The kind of a changed path, so the report can split a feature's lines into source code, test code, documentation, vendored
dependencies and everything else. The checks run in KINDS' priority order (vendored, docs, tests, src) and the first match wins;
names compare case-insensitively except the `…Tests/` folder suffix and the camel-case `FooTest.java`-style file names, whose
capital letter marks the word boundary (so `Latest.java` stays source)."""
import re

VENDORED, DOCS, TESTS, SRC, OTHER = "vendored", "docs", "tests", "src", "other"
KINDS = (SRC, TESTS, DOCS, VENDORED, OTHER)

VENDORED_SEGMENTS = frozenset(("node_modules", "vendor", "vendors", "third_party", "third-party", "bower_components",
                               "jspm_packages", "pods", "carthage", ".venv", "venv", "site-packages"))
LOCKFILES = frozenset(("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
                       "poetry.lock", "pipfile.lock", "uv.lock", "pdm.lock", "cargo.lock", "gemfile.lock", "composer.lock",
                       "podfile.lock", "package.resolved", "go.sum", "flake.lock", "packages.lock.json", "mix.lock",
                       "pubspec.lock", "gradle.lockfile"))
MINIFIED_SUFFIXES = (".min.js", ".min.css")
DOC_EXTENSIONS = frozenset((".md", ".mdx", ".markdown", ".rst", ".adoc", ".asciidoc"))
DOC_FOLDERS = frozenset(("docs", "doc", "documentation"))
TEST_FOLDERS = frozenset(("test", "tests", "__tests__", "spec", "specs", "testing", "e2e"))
TEST_FOLDER_SUFFIX = "Tests"  # case-sensitive: `MyAppTests/`
CODE_EXTENSIONS = frozenset((".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".java", ".kt",
                             ".kts", ".scala", ".groovy", ".go", ".rs", ".rb", ".php", ".swift", ".m", ".mm", ".h", ".hh", ".hpp",
                             ".c", ".cc", ".cpp", ".cxx", ".cs", ".fs", ".vb", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj",
                             ".lua", ".pl", ".pm", ".r", ".jl", ".sql", ".sh", ".bash", ".zsh", ".ps1", ".html", ".htm", ".css",
                             ".scss", ".sass", ".less", ".proto", ".graphql"))
TEST_NAME = re.compile(r"test_.*\.py|.*_test\.py|.*_test\.go|.*\.(test|spec)\.(js|jsx|ts|tsx|mjs|cjs)|.*_spec\.rb", re.IGNORECASE)
CAMEL_TEST_NAME = re.compile(r".*(Test\.(java|kt|swift|cs|php)|Tests\.(java|kt|swift|cs))")

RULES = {
    SRC: "code files by extension (e.g. .py, .ts, .go, .swift, .java, .sql, .sh, .html, .css) outside test locations",
    TESTS: "code files under a test folder (test, tests, __tests__, spec, specs, testing, e2e, …Tests) or named like a test "
           "(test_*.py, *_test.go, *.test.ts, *.spec.js, FooTest.java, FooTests.swift, *_spec.rb)",
    DOCS: "documentation: .md, .mdx, .markdown, .rst, .adoc, .asciidoc files and anything under a docs, doc or documentation "
          "folder",
    VENDORED: "third-party code and generated pins: node_modules, vendor, third_party, Pods, .venv and similar folders, "
              "lockfiles (package-lock.json, yarn.lock, Cargo.lock, go.sum, …) and minified .min.js / .min.css files",
    OTHER: "everything else: configuration, CI, JSON/YAML data, fixtures, images",
}


def _extension(name):
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def _is_vendored(segments, name):
    lowered = name.lower()
    return (any(segment.lower() in VENDORED_SEGMENTS for segment in segments) or lowered in LOCKFILES
            or lowered.endswith(MINIFIED_SUFFIXES))


def _is_test(folders, name):
    return (any(folder.lower() in TEST_FOLDERS or folder.endswith(TEST_FOLDER_SUFFIX) for folder in folders)
            or TEST_NAME.fullmatch(name) is not None or CAMEL_TEST_NAME.fullmatch(name) is not None)


def kind_of(path):
    """The kind (one of KINDS) of the root-relative `path`."""
    segments = path.split("/")
    folders, name = segments[:-1], segments[-1]
    extension = _extension(name)
    if _is_vendored(segments, name):
        return VENDORED
    if extension in DOC_EXTENSIONS or any(folder.lower() in DOC_FOLDERS for folder in folders):
        return DOCS
    if extension not in CODE_EXTENSIONS:
        return OTHER
    return TESTS if _is_test(folders, name) else SRC

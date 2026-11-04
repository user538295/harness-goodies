"""`.estimation.json` at the project root: read (the empty document when absent) and whole-file write. A write goes to a temp
`.json` next to the file, is checked with `check-jsonschema` against the shipped schema when that tool is on PATH, and only then
replaces the file (`os.replace`); a failed check leaves the old file and removes the temp."""
import contextlib
import json
import os
import tempfile

from estimation.errors import EstimateError

FILE_NAME = ".estimation.json"
FORMAT_VERSION = 1
DEFAULT_SCALE = [0, 0.5, 1, 2, 3, 5, 8, 13, 20, 40, 100, 200, 500, 1000]
VALIDATOR_NAME = "check-jsonschema"
VALIDATOR_SUCCESS = 0
JSON_INDENT = 2
TEMP_PREFIX = ".estimation."
TEMP_SUFFIX = ".json"
LOCATION_MARK = "::"  # check-jsonschema prints `<file>::<JSON path>: <message>`


def to_json(value):
    return json.dumps(value, indent=JSON_INDENT, ensure_ascii=False) + "\n"


def empty_document():
    return {"version": FORMAT_VERSION, "scale": list(DEFAULT_SCALE), "features": []}


class SchemaCheck:
    """Runs `check-jsonschema --schemafile <schema> <file>`; skipped when `which` cannot find it."""

    def __init__(self, schema_path, which, run):
        self.schema_path = schema_path
        self.which = which
        self.run = run

    def __call__(self, path):
        validator = self.which(VALIDATOR_NAME)
        if validator is None:
            return
        result = self.run([validator, "--schemafile", self.schema_path, path], os.path.dirname(path))
        if result.code != VALIDATOR_SUCCESS:
            raise EstimateError("%s not saved, it fails %s: %s" % (FILE_NAME, os.path.basename(self.schema_path),
                                                                  self._errors(result, path)))

    @staticmethod
    def _errors(result, path):
        """The error lines only, each without the temp file's name, joined into one line."""
        lines = [line.strip() for line in (result.stdout + result.stderr).splitlines()[1:] if line.strip()]
        prefix = path + LOCATION_MARK
        return "; ".join(line[len(prefix):] if line.startswith(prefix) else line for line in lines) or "exit %d" % result.code


class EstimationStore:
    def __init__(self, root, check):
        self.path = os.path.join(root, FILE_NAME)
        self.check = check

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            return empty_document()
        except (OSError, ValueError) as error:
            raise EstimateError("cannot read %s: %s" % (FILE_NAME, error)) from error
        if not isinstance(document, dict) or not isinstance(document.get("features"), list) \
                or not isinstance(document.get("scale"), list):
            raise EstimateError("cannot read %s: not an object with a scale and a features list" % FILE_NAME)
        return document

    def save(self, document):
        descriptor, temp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=os.path.dirname(self.path))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(to_json(document))
            self.check(temp_path)
            os.replace(temp_path, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)

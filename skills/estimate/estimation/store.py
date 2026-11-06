"""The estimate files under `.harness-goodies/` at the project root (C2): the scale file `estimation.json` and one file per feature
in `estimations/`, each with its `version`. `load` reads them into one document (the default scale and anchor and no features when
absent; a scale file without an anchor has it off; each feature without its `version` and with its file name under FILE_KEY).
`create` writes a new feature's file and `update` a stored one's, moving it to its new name first when its name changes; either
writes the scale file, with the default anchor, when there is none yet. `remove`
deletes one. A write goes to a temp `.json` next to its file, is checked with `check-jsonschema` against its shipped schema when
that tool is on PATH, and replaces the file (`os.replace`) only once every check of the save passed; a failed check leaves every
file as it was and removes the temps."""
import contextlib
import json
import os
import tempfile

from estimation.anchor import ANCHOR_KEY, DEFAULT_ANCHOR, check
from estimation.errors import EstimateError
from estimation.features import FILE_KEY, ID_KEY
from estimation.naming import FILE_SUFFIX, day_of_file, file_name

DATA_FOLDER = ".harness-goodies"
SCALE_FILE = "estimation.json"
FEATURES_FOLDER = "estimations"
SCALE_SCHEMA = "estimation.schema.json"
FEATURE_SCHEMA = "feature.schema.json"
FORMAT_VERSION = 1
VERSION_KEY = "version"
SCALE_KEY = "scale"
DEFAULT_SCALE = [0, 0.5, 1, 2, 3, 5, 8, 13, 20, 40, 100, 200, 500, 1000]
VALIDATOR_NAME = "check-jsonschema"
VALIDATOR_SUCCESS = 0
JSON_INDENT = 2
TEMP_PREFIX = ".estimation."
HIDDEN_PREFIX = "."  # temp files, never features
LOCATION_MARK = "::"  # check-jsonschema prints `<file>::<JSON path>: <message>`


def to_json(value):
    return json.dumps(value, indent=JSON_INDENT, ensure_ascii=False) + "\n"


def empty_document():
    return {VERSION_KEY: FORMAT_VERSION, SCALE_KEY: list(DEFAULT_SCALE), ANCHOR_KEY: dict(DEFAULT_ANCHOR), "features": []}


def _shown(*parts):
    """A data file as messages name it: relative to the project root."""
    return "/".join((DATA_FOLDER, *parts))


class SchemaCheck:
    """Runs `check-jsonschema --schemafile <schema> <file>` with a schema of `schema_folder`; skipped when `which` cannot find it."""

    def __init__(self, schema_folder, which, run):
        self.schema_folder = schema_folder
        self.which = which
        self.run = run

    def __call__(self, path, schema, target):
        """Checks the temp file `path` that is to become the data file `target`."""
        validator = self.which(VALIDATOR_NAME)
        if validator is None:
            return
        result = self.run([validator, "--schemafile", os.path.join(self.schema_folder, schema), path], os.path.dirname(path))
        if result.code != VALIDATOR_SUCCESS:
            raise EstimateError("%s not saved, it fails %s: %s" % (target, schema, self._errors(result, path)))

    @staticmethod
    def _errors(result, path):
        """The error lines only, each without the temp file's name, joined into one line."""
        lines = [line.strip() for line in (result.stdout + result.stderr).splitlines()[1:] if line.strip()]
        prefix = path + LOCATION_MARK
        return "; ".join(line[len(prefix):] if line.startswith(prefix) else line for line in lines) or "exit %d" % result.code


class EstimationStore:
    def __init__(self, root, check):
        self.root = root
        self.check = check

    def _path(self, *parts):
        return os.path.join(self.root, DATA_FOLDER, *parts)

    def _read(self, *parts):
        """The JSON object of one data file, None when it is absent; refused when it is not of FORMAT_VERSION."""
        try:
            with open(self._path(*parts), encoding="utf-8") as handle:
                content = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise EstimateError("cannot read %s: %s" % (_shown(*parts), error)) from error
        if not isinstance(content, dict):
            raise EstimateError("cannot read %s: not a JSON object" % _shown(*parts))
        if content.get(VERSION_KEY) != FORMAT_VERSION:
            raise EstimateError("cannot read %s: version %s (expected %d)" % (_shown(*parts), json.dumps(content.get(VERSION_KEY)),
                                                                               FORMAT_VERSION))
        return content

    def _feature_files(self):
        try:
            files = os.listdir(self._path(FEATURES_FOLDER))
        except FileNotFoundError:
            return []
        return sorted(file for file in files if file.endswith(FILE_SUFFIX) and not file.startswith(HIDDEN_PREFIX))

    def _feature(self, file):
        content = self._read(FEATURES_FOLDER, file)
        return {**{key: value for key, value in content.items() if key != VERSION_KEY}, FILE_KEY: file}

    def load(self):
        document = empty_document()
        settings = self._read(SCALE_FILE)
        if settings is not None:
            if not isinstance(settings.get(SCALE_KEY), list):
                raise EstimateError("cannot read %s: no scale list" % _shown(SCALE_FILE))
            document[SCALE_KEY] = settings[SCALE_KEY]
            document[ANCHOR_KEY] = settings.get(ANCHOR_KEY)
            try:
                check(document[ANCHOR_KEY], document[SCALE_KEY])
            except EstimateError as error:
                raise EstimateError("cannot read %s: %s" % (_shown(SCALE_FILE), error)) from error
        document["features"] = [self._feature(file) for file in self._feature_files()]
        return document

    def create(self, feature, day):
        """Writes a new `feature` to the file named from its first `day`, its id and its name; returns that file name."""
        file = file_name(day, feature[ID_KEY], feature["name"])
        self._refuse_taken(file)
        self._write([*self._ensure_scale(), self._feature_write(file, feature)])
        return file

    def update(self, feature):
        """Rewrites a stored `feature`, renaming its file when its id or name changed; returns its file name."""
        old = feature[FILE_KEY]
        file = file_name(day_of_file(old), feature[ID_KEY], feature["name"])
        if file != old:
            self._refuse_taken(file)
        self._write([*self._ensure_scale(), self._feature_write(file, feature)], None if file == old else old)
        return file

    def remove(self, feature):
        """Deletes the file of a stored `feature`."""
        os.unlink(self._path(FEATURES_FOLDER, feature[FILE_KEY]))

    def _refuse_taken(self, file):
        """Refuses to write a feature to `file` when another feature's file has that name."""
        if os.path.exists(self._path(FEATURES_FOLDER, file)):
            raise EstimateError("%s is the file of '%s'; give this feature another name" % (file, self._feature(file)["name"]))

    def _ensure_scale(self):
        """The write of the default scale file when there is none yet (the first write), else nothing."""
        if os.path.exists(self._path(SCALE_FILE)):
            return []
        return [((SCALE_FILE,), {VERSION_KEY: FORMAT_VERSION, SCALE_KEY: list(DEFAULT_SCALE), ANCHOR_KEY: dict(DEFAULT_ANCHOR)},
                 SCALE_SCHEMA)]

    @staticmethod
    def _feature_write(file, feature):
        """The write of `feature` to its `file`: its version first, without its FILE_KEY."""
        stored = {VERSION_KEY: FORMAT_VERSION, **{key: value for key, value in feature.items() if key != FILE_KEY}}
        return (FEATURES_FOLDER, file), stored, FEATURE_SCHEMA

    def _write(self, writes, renamed=None):
        """Writes each (path parts, content, schema) to a checked temp file, then replaces the files once every check passed. The
        feature file `renamed` is first moved onto the new feature file, so one file holds the feature at all times."""
        temps = []
        try:
            for parts, content, schema in writes:
                folder = os.path.dirname(self._path(*parts))
                os.makedirs(folder, exist_ok=True)
                descriptor, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=FILE_SUFFIX, dir=folder)
                temps.append(temp)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(to_json(content))
                self.check(temp, schema, _shown(*parts))
            if renamed is not None:
                os.replace(self._path(FEATURES_FOLDER, renamed), self._path(*writes[-1][0]))
            for (parts, _, _), temp in zip(writes, temps):
                os.replace(temp, self._path(*parts))
        finally:
            for temp in temps:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temp)

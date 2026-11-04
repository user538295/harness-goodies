"""The project a run works on: its root (the git top-level of the working folder, else the working folder) and the C2 path rule
(paths inside the project are stored root-relative with `/`, paths outside it absolute), and the day the file of a stored path
was created."""
import os
from datetime import date

GIT_SUCCESS = 0


class Project:
    def __init__(self, cwd, run):
        self.cwd = os.path.realpath(cwd)
        self.run = run
        self.root = self._git_top_level() or self.cwd

    def _git_top_level(self):
        result = self.run(["git", "rev-parse", "--show-toplevel"], self.cwd)
        top_level = result.stdout.strip()
        return os.path.realpath(top_level) if result.code == GIT_SUCCESS and top_level else None

    def absolute(self, given):
        """`given` (relative to the working folder, or absolute) as an absolute path whose folders are resolved; the file name itself
        stays as given, so a linked document keeps its own name."""
        joined = os.path.normpath(os.path.join(self.cwd, given))
        return os.path.join(os.path.realpath(os.path.dirname(joined)), os.path.basename(joined))

    def stored(self, given):
        """`given` in its stored form: root-relative with `/` when inside the project, else absolute."""
        absolute = self.absolute(given)
        try:
            relative = os.path.relpath(absolute, self.root)
        except ValueError:  # another drive on Windows
            return absolute
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return absolute
        return relative.replace(os.sep, "/")

    def located(self, stored):
        """The absolute path of a stored path."""
        return stored if os.path.isabs(stored) else os.path.join(self.root, *stored.split("/"))

    def created_day(self, stored):
        """The day the file of a stored path was created (its birth time; Linux keeps none, so its last change), or None when
        there is no such file."""
        try:
            status = os.stat(self.located(stored))
        except (FileNotFoundError, NotADirectoryError):
            return None
        return date.fromtimestamp(getattr(status, "st_birthtime", status.st_mtime))

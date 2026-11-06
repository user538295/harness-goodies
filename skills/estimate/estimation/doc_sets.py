"""`find-sets`: the planning-doc sets of the project and whether each is delivered. A planning doc is a `.md` file named
`…brief.md`, `…team-plan.md`, `…plan.md` or `…tasks.md` (after `-`, `_` or nothing); inside git the tracked and untracked-not-ignored
files, outside git a walk that skips hidden and dependency folders. Docs join one set when they share a folder and a stem, when a
front-matter `brief`/`team-plan`/`plan`/`tasks` value names the other, or when they share a folder and a normalized first `# `
heading; links in the text (a References section cites other features) never join docs. A set's plan doc is the doc whose
doc-stats holds tasks, a tasks file before a team plan before a plan; the set is delivered when every task of it is ticked."""
import os
import posixpath
import re
from dataclasses import dataclass

from estimation.doc_stats import doc_stats
from estimation.errors import EstimateError
from estimation.line_kinds import VENDORED_SEGMENTS

BRIEF, TEAM_PLAN, PLAN, TASKS = "brief", "team-plan", "plan", "tasks"
PLAN_DOC_KINDS = (TASKS, TEAM_PLAN, PLAN)  # the kinds doc-stats reads, the preferred plan doc first
PLANNING_NAME = re.compile(r"(?:^|[-_])(brief|team-plan|plan|tasks)\.md$", re.IGNORECASE)  # leftmost: team-plan wins over plan
FRONT_MATTER_MARK = "---"
LINK_LINE = re.compile(r"^(brief|team-plan|plan|tasks)\s*:(.*)$")
VALUE_COMMENT = re.compile(r"\s#.*$")
QUOTES = "\"'"
HEADING_MARK = "# "
TITLE_LABEL = re.compile(r"^(?:feature brief|brief):\s*", re.IGNORECASE)
TITLE_SUFFIX = re.compile(r"\s[—–-]\s(?:team plan|task breakdown|plan|tasks|brief)$", re.IGNORECASE)
HIDDEN_PREFIX = "."
PLAN_FIELDS = ("phases", "tasks", "completed", "estimatedHours")
LINK_NOT_FOUND = "%s: front matter %s: %s not found"
SEVERAL_TASK_LISTS = "several task lists: %s"
NO_PLAN_DOC = "no plan or tasks file with tasks"


@dataclass(frozen=True)
class PlanningDoc:
    path: str  # project-relative
    kind: str
    stem: str  # the name before `[-_]<kind>.md`, lower case
    links: tuple[tuple[str, str], ...]  # front-matter (key, value)
    title: str | None  # the normalized first `# ` heading

    @property
    def folder(self):
        return posixpath.dirname(self.path)


def _walked(root):
    """The root-relative paths of the files under `root`, outside hidden and dependency folders."""
    paths = []
    for folder, folders, files in os.walk(root):
        folders[:] = [name for name in folders if not name.startswith(HIDDEN_PREFIX) and name.lower() not in VENDORED_SEGMENTS]
        relative = os.path.relpath(folder, root).replace(os.sep, "/")
        paths += [name if relative == os.curdir else relative + "/" + name for name in files]
    return paths


def _listed(project, history):
    """(the root-relative paths of the project's files, the reason they could not be listed or none)."""
    if not project.in_git:
        return _walked(project.root), []
    try:
        return history.files(), []
    except EstimateError as error:
        return [], [str(error)]


def _title(line):
    title = " ".join(line[len(HEADING_MARK):].split())
    title = TITLE_SUFFIX.sub("", TITLE_LABEL.sub("", title)).casefold()
    return title or None


def _parsed(path, match, text):
    lines = text.splitlines()
    links, body = [], lines
    if lines and lines[0].strip() == FRONT_MATTER_MARK:
        end = next((index for index in range(1, len(lines)) if lines[index].strip() == FRONT_MATTER_MARK), None)
        if end is not None:
            for line in lines[1:end]:
                link = LINK_LINE.match(line)
                value = link and VALUE_COMMENT.sub("", link.group(2)).strip().strip(QUOTES)
                if value:
                    links.append((link.group(1), value))
            body = lines[end + 1:]
    heading = next((line for line in body if line.startswith(HEADING_MARK)), None)
    name = posixpath.basename(path)
    return PlanningDoc(path, match.group(1).lower(), name[:match.start()].lower(), tuple(links),
                       heading and _title(heading))


def _planning_docs(project, paths):
    docs = []
    for path in sorted(paths):
        match = PLANNING_NAME.search(posixpath.basename(path))
        absolute = project.located(path)
        if match is None or not os.path.isfile(absolute):
            continue
        try:
            with open(absolute, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as error:
            raise EstimateError("cannot read %s: %s" % (path, error.strerror or error)) from error
        docs.append(_parsed(path, match, text))
    return docs


class _Groups:
    """Union-find over the docs' paths, with the front-matter values that named no doc."""

    def __init__(self, docs):
        self.parent = {doc.path: doc.path for doc in docs}
        self.missing = {doc.path: [] for doc in docs}

    def root(self, path):
        while self.parent[path] != path:
            self.parent[path] = self.parent[self.parent[path]]
            path = self.parent[path]
        return path

    def join(self, first, second):
        self.parent[self.root(first)] = self.root(second)

    def join_alike(self, docs, key):
        """Joins the docs of one folder whose `key` is the same (and not None)."""
        firsts = {}
        for doc in docs:
            value = key(doc)
            if value is not None:
                self.join(firsts.setdefault((doc.folder, value), doc.path), doc.path)

    def sets(self):
        members = {}
        for path in self.parent:
            members.setdefault(self.root(path), []).append(path)
        return sorted(sorted(paths) for paths in members.values())


def _target(doc, value, paths, by_name):
    """The planning doc a front-matter value names: relative to the doc's folder, else to the project root, else the planning doc
    of that basename in the doc's own folder (values go stale when a doc moves, e.g. from Backlog/ to Completed/)."""
    for candidate in (posixpath.normpath(posixpath.join(doc.folder, value)), posixpath.normpath(value)):
        if candidate in paths:
            return candidate
    return by_name.get((doc.folder, posixpath.basename(value)))


def _grouped(docs):
    groups = _Groups(docs)
    groups.join_alike(docs, lambda doc: doc.stem)
    groups.join_alike(docs, lambda doc: doc.title)
    paths = {doc.path for doc in docs}
    by_name = {(doc.folder, posixpath.basename(doc.path)): doc.path for doc in docs}
    for doc in docs:
        for key, value in doc.links:
            target = _target(doc, value, paths, by_name)
            if target is None:
                groups.missing[doc.path].append(LINK_NOT_FOUND % (doc.path, key, value))
            else:
                groups.join(doc.path, target)
    return groups


def _described(paths, kinds, missing, project, plan_progress, run):
    """The `{"docs", "planDoc", "plan", "delivered", "missing"}` of one set of (sorted) `paths`."""
    stats = {path: doc_stats(project.located(path), plan_progress, run) for path in paths if kinds[path] != BRIEF}
    plan_doc = None
    for kind in PLAN_DOC_KINDS:
        holders = [path for path in paths if kinds[path] == kind and (stats[path]["tasks"] or 0) > 0]
        if holders:
            plan_doc = holders[0]
            if len(holders) > 1:
                missing.append(SEVERAL_TASK_LISTS % ", ".join(holders))
            break
    if plan_doc is None:
        return {"docs": paths, "planDoc": None, "plan": None, "delivered": False, "missing": missing + [NO_PLAN_DOC]}
    plan = {field: stats[plan_doc][field] for field in PLAN_FIELDS}
    name = posixpath.basename(plan_doc)
    return {"docs": paths, "planDoc": plan_doc, "plan": plan, "delivered": plan["completed"] == plan["tasks"],
            "missing": missing + ["%s: %s" % (name, line) for line in stats[plan_doc]["missing"]]}


def doc_sets(project, history, plan_progress, run):
    """The `find-sets` payload: `{"sets", "missing"}`, `missing` naming what kept the project's files from being listed."""
    paths, missing = _listed(project, history)
    docs = _planning_docs(project, paths)
    groups = _grouped(docs)
    kinds = {doc.path: doc.kind for doc in docs}
    return {"sets": [_described(members, kinds, [line for path in members for line in groups.missing[path]], project,
                                plan_progress, run) for members in groups.sets()],
            "missing": missing}

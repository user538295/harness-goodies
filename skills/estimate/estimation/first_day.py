"""The first day of a new feature (C2), which names its file: the earliest of the day git first added each of its docs (following
renames), the day each doc git does not hold was created (Project.created_day), the author day of each of its commits, and today."""
import os


def _doc_day(doc, project, history):
    """A stored doc path's first day: git's for a doc inside the project (stored relative), else its file's."""
    added = None if os.path.isabs(doc) else history.added_at(doc)
    return project.created_day(doc) if added is None else added.date()


def first_day(feature, project, history, today):
    days = [today, *(moment.date() for moment in history.authored_at(feature["sources"]["commits"]).values())]
    days += [day for day in (_doc_day(doc, project, history) for doc in feature["sources"]["docs"]) if day is not None]
    return min(days)

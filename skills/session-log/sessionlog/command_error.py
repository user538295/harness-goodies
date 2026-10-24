"""The runtime error of a command (C1): the entry prints its message as `session-log: <msg>` and exits 1."""


class CommandError(Exception):
    """A runtime error with its facts; the entry prints it and exits 1."""

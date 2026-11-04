"""The two failures a command can end with; `cli` prints either as one `estimate:` line."""


class ArgumentError(Exception):
    """Bad command-line arguments: exit code 2."""


class EstimateError(Exception):
    """A runtime failure: exit code 1."""

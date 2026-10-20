from datetime import datetime, timezone
from pathlib import Path


class PIDFile:
    def __init__(self, watcher_dir: Path) -> None:
        self._path = watcher_dir / "watcher.pid"

    def _format_lines(self, pid: int, nonce: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return f"{pid}:{nonce}\n{ts}\n"

    def write(self, pid: int, nonce: str) -> None:
        self._path.write_text(self._format_lines(pid, nonce))

    def update_heartbeat(self, pid: int, nonce: str) -> None:
        tmp = self._path.with_suffix(".pid.tmp")
        tmp.write_text(self._format_lines(pid, nonce))
        tmp.replace(self._path)

    def read(self) -> tuple[int, str, datetime] | None:
        try:
            lines = self._path.read_text().splitlines()
        except OSError:
            return None
        if len(lines) < 2:
            return None
        parts = lines[0].split(":", maxsplit=1)
        if len(parts) != 2:
            return None
        try:
            pid = int(parts[0])
        except ValueError:
            return None
        nonce = parts[1]
        try:
            heartbeat_dt = datetime.fromisoformat(lines[1])
        except ValueError:
            return None
        return (pid, nonce, heartbeat_dt)

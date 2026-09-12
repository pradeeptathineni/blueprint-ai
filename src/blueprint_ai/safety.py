from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

MAX_CONFIG_BYTES = 256_000
MAX_MANIFEST_BYTES = 2_000_000
MAX_MODEL_FILE_BYTES = 512_000
MAX_TOOL_OUTPUT_BYTES = 4_000_000

_ANSI = re.compile(rb"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def safe_regular_file(
    path: Path, root: Path | None = None, *, follow_symlinks: bool = False
) -> bool:
    """Return true only for a bounded-trust regular file within root."""
    try:
        info = path.stat() if follow_symlinks else path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return False
        if root is not None:
            resolved_root = root.resolve()
            resolved = path.resolve()
            if not is_relative_to(resolved, resolved_root):
                return False
            if not follow_symlinks and path.is_symlink():
                return False
        return True
    except OSError:
        return False


def read_bytes_bounded(path: Path, limit: int, *, root: Path | None = None) -> bytes:
    if not safe_regular_file(path, root):
        raise OSError(f"not a safe regular file: {path}")
    # Recheck the opened descriptor: an untrusted path can change after preflight.
    # NONBLOCK prevents a replacement FIFO from hanging before fstat can reject it.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    if (
        root is not None
        and os.open in os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    ):
        try:
            relative = path.absolute().relative_to(root.absolute())
        except ValueError as exc:
            raise OSError(f"read target escapes safety root: {path}") from exc
        if ".." in relative.parts or not relative.parts:
            raise OSError(f"read target escapes safety root: {path}")
        # Pin every directory inside the authorized root; replacing a parent with
        # an outside symlink must not redirect the final open.
        directory = os.open(root.resolve(), flags | os.O_DIRECTORY)
        try:
            for part in relative.parts[:-1]:
                child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(relative.name, flags, dir_fd=directory)
        finally:
            os.close(directory)
    else:
        descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"not a safe regular file: {path}")
        if info.st_size > limit:
            raise ValueError(f"{path.name} exceeds the {limit}-byte safety limit")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{path.name} exceeds the {limit}-byte safety limit")
    return data


def read_text_bounded(
    path: Path,
    limit: int,
    *,
    root: Path | None = None,
    errors: str = "strict",
) -> str:
    return read_bytes_bounded(path, limit, root=root).decode("utf-8", errors=errors)


def sanitize_terminal_bytes(value: bytes, limit: int = MAX_TOOL_OUTPUT_BYTES) -> tuple[str, bool]:
    truncated = len(value) > limit
    clean = _ANSI.sub(b"", value[:limit]).decode("utf-8", errors="replace")
    clean = _CONTROL.sub("�", clean)
    if truncated:
        clean += "\n[blueprint-ai: output truncated]"
    return clean, truncated


def sanitize_label(value: str, limit: int = 512) -> str:
    return _CONTROL.sub("�", value.replace("\r", "\\r").replace("\n", "\\n"))[:limit]


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool


@dataclass(frozen=True)
class RawProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_truncated: bool


def controlled_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed = {
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "TMP",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(
        {
            "CI": "1",
            "NO_COLOR": "1",
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONNOUSERSITE": "1",
        }
    )
    env.update(extra or {})
    return env


def _read_pipe(pipe, sink: bytearray, limit: int) -> None:
    try:
        while chunk := pipe.read(65_536):
            remaining = max(0, limit + 1 - len(sink))
            if remaining:
                sink.extend(chunk[:remaining])
    finally:
        pipe.close()


def run_process_bytes(
    command: list[str],
    cwd: Path,
    timeout: float,
    *,
    output_limit: int = MAX_TOOL_OUTPUT_BYTES,
    env: dict[str, str] | None = None,
) -> RawProcessResult:
    isolated_home = tempfile.TemporaryDirectory(prefix="blueprint-ai-home-")
    if env is None:
        cache = Path(isolated_home.name) / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        env = controlled_env(
            {
                "HOME": isolated_home.name,
                "XDG_CACHE_HOME": str(cache),
            }
        )
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except OSError:
        isolated_home.cleanup()
        raise
    stdout = bytearray()
    stderr = bytearray()
    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(
            target=_read_pipe, args=(process.stdout, stdout, output_limit), daemon=True
        ),
        threading.Thread(
            target=_read_pipe, args=(process.stderr, stderr, output_limit), daemon=True
        ),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            process.kill()
        process.wait(timeout=5)
    else:
        # A hostile tool must not leave background children holding resources after it exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            pass
    for reader in readers:
        reader.join(timeout=5)
    result = RawProcessResult(
        124 if timed_out else process.returncode,
        bytes(stdout[:output_limit]),
        bytes(stderr[:output_limit]),
        timed_out,
        len(stdout) > output_limit or len(stderr) > output_limit,
    )
    isolated_home.cleanup()
    return result


def run_process(
    command: list[str],
    cwd: Path,
    timeout: float,
    *,
    output_limit: int = MAX_TOOL_OUTPUT_BYTES,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    raw = run_process_bytes(command, cwd, timeout, output_limit=output_limit, env=env)
    stdout, _ = sanitize_terminal_bytes(raw.stdout, output_limit)
    stderr, _ = sanitize_terminal_bytes(raw.stderr, output_limit)
    if raw.output_truncated:
        stdout += "\n[blueprint-ai: output truncated]"
    return ProcessResult(
        raw.returncode,
        stdout,
        stderr,
        raw.timed_out,
        raw.output_truncated,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write_text(
    target: Path,
    content: str,
    *,
    overwrite: bool = True,
    root: Path | None = None,
) -> str:
    """Write and fsync a regular file without following a target symlink."""
    if root is not None:
        resolved_root = root.resolve()
        if not is_relative_to(target.resolve(), resolved_root):
            raise OSError(f"write target escapes safety root: {target}")
        try:
            relative = target.absolute().relative_to(root.absolute())
        except ValueError as exc:
            raise OSError(f"write target escapes safety root: {target}") from exc
        current = root.absolute()
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise OSError(f"write target crosses symlink parent: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise OSError(f"refusing to replace non-regular file: {target}")
    if not overwrite and os.path.lexists(target):
        raise FileExistsError(target)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target, follow_symlinks=False)
            os.unlink(temporary)
        try:
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return sha256_bytes(content.encode())

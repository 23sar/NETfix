"""Command execution abstraction for safe, typed, mockable network remediation.

Enforces execution safety invariants:
1. shell=False strictly enforced; raw shell strings prohibited.
2. Live command execution disabled by default; requires explicit authorization.
3. Subprocess arguments strictly typed and bounded by timeouts.
4. Unit tests execute against MockCommandExecutor with zero system mutations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("netfix.controller.executor")


class LiveExecutionNotAuthorizedError(RuntimeError):
    """Raised when an attempt is made to execute live commands without explicit authorization."""
    pass


class InvalidCommandError(ValueError):
    """Raised when a command fails typed validation (e.g. non-string, not in allowlist)."""
    pass


@dataclass
class CommandResult:
    """Structured result of an executed command."""

    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def success(self) -> bool:
        """True if command exited with code 0 and did not time out."""
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> Dict[str, Any]:
        """Convert command result to dictionary."""
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": self.duration_s,
            "timed_out": self.timed_out,
            "success": self.success,
        }


class BaseCommandExecutor(ABC):
    """Abstract interface for executing system network commands."""

    is_safe_mock: bool = False

    @abstractmethod
    def execute(self, cmd: Sequence[str], timeout: float = 5.0) -> CommandResult:
        """Execute a typed command sequence with bounded timeout.

        Args:
            cmd: Sequence of argument strings (argv).
            timeout: Maximum allowed execution time in seconds.

        Returns:
            CommandResult with exit code, stdout, stderr, and duration.
        """
        pass


class MockCommandExecutor(BaseCommandExecutor):
    """Deterministic mock executor for unit testing without system mutations."""

    is_safe_mock: bool = True

    def __init__(self) -> None:
        self.calls: List[Tuple[List[str], float]] = []
        self._canned_responses: Dict[str, CommandResult] = {}
        self._timeout_prefixes: List[str] = []
        self._callback: Optional[Callable[[Sequence[str], float], Optional[CommandResult]]] = None

    def set_response(
        self,
        cmd_prefix: Union[str, Sequence[str]],
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        duration_s: float = 0.001,
        timed_out: bool = False,
    ) -> None:
        """Register a canned response matching a command prefix string or tuple."""
        key = " ".join(cmd_prefix) if isinstance(cmd_prefix, (list, tuple)) else str(cmd_prefix)
        self._canned_responses[key] = CommandResult(
            command=[],  # Populated at execution time
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration_s,
            timed_out=timed_out,
        )

    def set_timeout(self, cmd_prefix: Union[str, Sequence[str]]) -> None:
        """Simulate a command timeout for commands matching prefix."""
        key = " ".join(cmd_prefix) if isinstance(cmd_prefix, (list, tuple)) else str(cmd_prefix)
        self._timeout_prefixes.append(key)

    def set_callback(
        self,
        callback: Callable[[Sequence[str], float], Optional[CommandResult]],
    ) -> None:
        """Register a dynamic callback for complex command simulation."""
        self._callback = callback

    def execute(self, cmd: Sequence[str], timeout: float = 5.0) -> CommandResult:
        cmd_list = [str(arg) for arg in cmd]
        self.calls.append((cmd_list, timeout))
        cmd_str = " ".join(cmd_list)

        # Check for simulated timeout
        for t_prefix in self._timeout_prefixes:
            if cmd_str.startswith(t_prefix):
                return CommandResult(
                    command=cmd_list,
                    returncode=124,
                    stdout="",
                    stderr=f"Command timed out after {timeout:.1f}s (simulated)",
                    duration_s=timeout,
                    timed_out=True,
                )

        # Check dynamic callback
        if self._callback is not None:
            cb_result = self._callback(cmd_list, timeout)
            if cb_result is not None:
                cb_result.command = cmd_list
                return cb_result

        # Check canned responses by longest matching prefix
        matching_key = None
        for key in self._canned_responses:
            if cmd_str.startswith(key):
                if matching_key is None or len(key) > len(matching_key):
                    matching_key = key

        if matching_key is not None:
            canned = self._canned_responses[matching_key]
            return CommandResult(
                command=cmd_list,
                returncode=canned.returncode,
                stdout=canned.stdout,
                stderr=canned.stderr,
                duration_s=canned.duration_s,
                timed_out=canned.timed_out,
            )

        # Default success response for unconfigured commands
        return CommandResult(
            command=cmd_list,
            returncode=0,
            stdout="",
            stderr="",
            duration_s=0.001,
            timed_out=False,
        )

    def clear(self) -> None:
        """Clear recorded calls and responses."""
        self.calls.clear()
        self._canned_responses.clear()
        self._timeout_prefixes.clear()
        self._callback = None


class SubprocessCommandExecutor(BaseCommandExecutor):
    """Subprocess executor with strict safety invariants for live operations.

    Invariants:
    1. Disabled by default: allow_live_execution must be explicitly True.
    2. shell=False strictly enforced.
    3. Binary must belong to allowlisted set ('ovs-ofctl', 'tc', 'ip', 'ping').
    4. Binary must resolve to validated absolute system path (SEC-M5-07).
    5. Timeout bounded to [0.5, 15.0] seconds.
    """

    is_safe_mock: bool = False
    ALLOWLISTED_BINARIES = frozenset({"ovs-ofctl", "tc", "ip", "ping"})
    DEFAULT_SECURE_BINARY_MAP: Dict[str, str] = {
        "ovs-ofctl": "/usr/bin/ovs-ofctl",
        "tc": "/sbin/tc",
        "ip": "/bin/ip",
        "ping": "/bin/ping",
    }
    ALLOWED_SYSTEM_DIRS = frozenset({"/bin", "/sbin", "/usr/bin", "/usr/sbin"})
    MIN_TIMEOUT_S: float = 0.5
    MAX_TIMEOUT_S: float = 15.0

    def __init__(
        self,
        allow_live_execution: bool = False,
        binary_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.allow_live_execution = allow_live_execution
        self.binary_map = dict(binary_map) if binary_map is not None else dict(self.DEFAULT_SECURE_BINARY_MAP)

    def execute(self, cmd: Sequence[str], timeout: float = 5.0) -> CommandResult:
        if not self.allow_live_execution:
            raise LiveExecutionNotAuthorizedError(
                "Live command execution is disabled by default. "
                "Explicit authorization (allow_live_execution=True) is required."
            )

        if not cmd:
            raise InvalidCommandError("Cannot execute empty command sequence.")

        cmd_list = [str(arg) for arg in cmd]
        binary = cmd_list[0].split("/")[-1]

        if binary not in self.ALLOWLISTED_BINARIES:
            raise InvalidCommandError(
                f"Binary '{binary}' is not allowlisted for remediation. "
                f"Allowed binaries: {sorted(list(self.ALLOWLISTED_BINARIES))}."
            )

        # SEC-M5-07: Resolve to validated absolute system path, avoiding ambient $PATH
        resolved_path = self.binary_map.get(binary)
        if not resolved_path or not Path(resolved_path).exists():
            for sdir in sorted(list(self.ALLOWED_SYSTEM_DIRS)):
                candidate = Path(sdir) / binary
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    resolved_path = str(candidate)
                    break

        if not resolved_path or not Path(resolved_path).is_absolute() or Path(resolved_path).parent.as_posix() not in self.ALLOWED_SYSTEM_DIRS:
            raise InvalidCommandError(
                f"Binary '{binary}' could not be resolved to a secure trusted system path "
                f"in {sorted(list(self.ALLOWED_SYSTEM_DIRS))}."
            )

        # Substitute validated absolute executable path
        cmd_list[0] = resolved_path

        # Clamp timeout to safety bounds
        effective_timeout = max(self.MIN_TIMEOUT_S, min(timeout, self.MAX_TIMEOUT_S))

        start_time = time.monotonic()
        try:
            proc = subprocess.run(
                cmd_list,
                shell=False,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            duration = time.monotonic() - start_time
            return CommandResult(
                command=cmd_list,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_s=duration,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            return CommandResult(
                command=cmd_list,
                returncode=124,
                stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr if isinstance(exc.stderr, str) else f"Command timed out after {effective_timeout:.1f}s",
                duration_s=duration,
                timed_out=True,
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return CommandResult(
                command=cmd_list,
                returncode=-1,
                stdout="",
                stderr=f"Execution failed: {exc}",
                duration_s=duration,
                timed_out=False,
            )

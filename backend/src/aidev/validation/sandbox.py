"""Runs the checks in Docker containers (ADR 009, 019), through the docker CLI.

Each validation gets two fresh volumes: /work holds the workspace and is mounted
read-only everywhere after it's filled, and /deps holds the installed requirements.
Only the install step has a network, and it runs uv alone, with source builds
disabled, so no code from a package runs while the network is up. Downloads go to one
shared cache volume: only uv writes to it, and nothing in it ever runs. Every check
then runs in its own container with no network, a read-only root, no capabilities, and
memory, CPU and process limits.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Protocol

from aidev.validation.workspace import Workspace

LABEL = "aidev.validation"
UV_CACHE = "aidev-sandbox-uv-cache"  # kept across validations; `prune` leaves it
UID = "10001"  # the image's sandbox user
DOCKER_FAILED = 125  # `docker run` exit code when Docker itself failed, not the command


class SandboxError(RuntimeError):
    """Docker failed: a broken sandbox, not a verdict on the code."""


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


class Session(Protocol):
    async def install(self, timeout_s: float) -> ExecResult: ...

    async def exec(self, argv: Sequence[str], timeout_s: float) -> ExecResult: ...


class Sandbox(Protocol):
    def session(self, workspace: Workspace) -> AbstractAsyncContextManager[Session]: ...


@dataclass(frozen=True)
class Limits:
    memory: str = "2g"  # pyright over the Stripe SDK peaks near 1 GB
    cpus: float = 1.0
    pids: int = 256
    tmp: str = "256m"


async def docker(
    *args: str, stdin: bytes | None = None, timeout_s: float | None = None
) -> ExecResult:
    """Run the docker CLI. On timeout the CLI is killed and `timed_out` is set; the
    caller removes the container, which killing the CLI doesn't."""
    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SandboxError("the docker CLI isn't installed or isn't on PATH") from exc
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout_s)
    except TimeoutError:
        timed_out = True
        proc.kill()
        out, err = await proc.communicate()
    return ExecResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out.decode(errors="replace"),
        stderr=err.decode(errors="replace"),
        duration_ms=(time.perf_counter() - start) * 1000,
        timed_out=timed_out,
    )


class DockerSandbox:
    def __init__(self, image: str, limits: Limits | None = None) -> None:
        self.image = image
        self.limits = limits or Limits()

    async def ensure_image(self) -> None:
        r = await docker("image", "inspect", "--format", "{{.Id}}", self.image)
        if r.exit_code != 0:
            raise SandboxError(
                f"sandbox image {self.image} not found. Build it from backend/: "
                "uv run python -m aidev.validation build-image"
            )

    @asynccontextmanager
    async def session(self, workspace: Workspace) -> AsyncIterator[Session]:
        await self.ensure_image()
        session = _DockerSession(self, uuid.uuid4().hex[:12])
        try:
            await session.start(workspace)
            yield session
        finally:
            await session.cleanup()


class _DockerSession:
    def __init__(self, sandbox: DockerSandbox, run_id: str) -> None:
        self._sandbox = sandbox
        self._id = run_id
        self._work = f"aidev-val-{run_id}-work"
        self._deps = f"aidev-val-{run_id}-deps"
        self._count = 0

    async def start(self, workspace: Workspace) -> None:
        for volume in (self._work, self._deps):
            _ok(await docker("volume", "create", "--label", f"{LABEL}={self._id}", volume))
        # The only write to /work: unpack the workspace, with no network.
        r = await self._run(
            ["tar", "-x", "-C", "/work"],
            mounts=[f"{self._work}:/work"],
            stdin=workspace.to_tar(),
            timeout_s=60,
        )
        _ok(r, "copying the workspace into the sandbox failed")

    async def install(self, timeout_s: float) -> ExecResult:
        # uv runs from /tmp with --no-config, so a uv.toml or pyproject.toml in the
        # workspace can't change the index or re-enable builds.
        argv = [
            "uv", "pip", "install", "--no-config", "--no-build",
            "--target", "/deps", "--python", "/usr/local/bin/python",
            "-r", "/work/requirements.txt",
        ]  # fmt: skip
        return await self._run(
            argv,
            mounts=[f"{self._work}:/work:ro", f"{self._deps}:/deps", f"{UV_CACHE}:/uv-cache"],
            network=True,
            timeout_s=timeout_s,
        )

    async def exec(self, argv: Sequence[str], timeout_s: float) -> ExecResult:
        return await self._run(
            argv,
            mounts=[f"{self._work}:/work:ro", f"{self._deps}:/deps:ro"],
            timeout_s=timeout_s,
        )

    async def _run(
        self,
        argv: Sequence[str],
        *,
        mounts: Sequence[str],
        timeout_s: float,
        network: bool = False,
        stdin: bytes | None = None,
    ) -> ExecResult:
        self._count += 1
        name = f"aidev-val-{self._id}-{self._count}"
        lim = self._sandbox.limits
        args = [
            "run", "--rm", "--name", name, "--label", f"{LABEL}={self._id}",
            "--user", UID, "--read-only", "--tmpfs", f"/tmp:rw,nosuid,nodev,size={lim.tmp}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", lim.memory, "--memory-swap", lim.memory,
            "--cpus", str(lim.cpus), "--pids-limit", str(lim.pids),
            "--network", "bridge" if network else "none",
        ]  # fmt: skip
        if stdin is not None:
            args.append("-i")
        for mount in mounts:
            args += ["-v", mount]
        r = await docker(*args, self._sandbox.image, *argv, stdin=stdin, timeout_s=timeout_s)
        if r.timed_out:
            await docker("rm", "-f", name, timeout_s=30)
        elif r.exit_code == DOCKER_FAILED:
            raise SandboxError(f"docker run failed: {r.stderr.strip()}")
        return r

    async def cleanup(self) -> None:
        with suppress(Exception):
            ps = await docker("ps", "-aq", "--filter", f"label={LABEL}={self._id}", timeout_s=30)
            if ids := ps.stdout.split():
                await docker("rm", "-f", *ids, timeout_s=60)
        with suppress(Exception):
            await docker("volume", "rm", "-f", self._work, self._deps, timeout_s=60)


def _ok(r: ExecResult, what: str = "docker failed") -> None:
    if r.exit_code != 0 or r.timed_out:
        raise SandboxError(f"{what}: {r.stderr.strip() or r.stdout.strip()}")


async def prune() -> int:
    """Remove containers and volumes that an interrupted validation left behind."""
    removed = 0
    ps = await docker("ps", "-aq", "--filter", f"label={LABEL}")
    if ids := ps.stdout.split():
        await docker("rm", "-f", *ids)
        removed += len(ids)
    vols = await docker("volume", "ls", "-q", "--filter", f"label={LABEL}")
    if names := vols.stdout.split():
        await docker("volume", "rm", "-f", *names)
        removed += len(names)
    return removed

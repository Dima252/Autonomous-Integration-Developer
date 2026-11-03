"""The sandbox against real Docker: its isolation, and the seeded-bug cases."""

import pytest

from aidev.config import get_settings
from aidev.validation.cases import apply, load_cases
from aidev.validation.sandbox import DockerSandbox, docker
from aidev.validation.specs import SpecLibrary
from aidev.validation.validator import Validator
from aidev.validation.workspace import Workspace

pytestmark = pytest.mark.sandbox

SETTINGS = get_settings()
CORPUS = SETTINGS.corpus_dir / "validation"
BASE = Workspace.from_dir(CORPUS / "base").files
CASES = load_cases(CORPUS / "cases.toml")
PROBE = Workspace({"requirements.txt": "", "probe.py": "x = 1\n"})
SPECS = SpecLibrary(SETTINGS.data_dir / "specs")  # shared, so each spec loads once
CONNECT = "import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3)"


@pytest.fixture
def sandbox() -> DockerSandbox:
    return DockerSandbox(SETTINGS.sandbox_image)


async def test_checks_have_no_network(sandbox: DockerSandbox) -> None:
    async with sandbox.session(PROBE) as session:
        r = await session.exec(["python", "-c", CONNECT, "1.1.1.1", "53"], timeout_s=30)
    assert r.exit_code != 0
    assert "Network is unreachable" in r.stderr or "OSError" in r.stderr


async def test_tests_reach_the_mocks_and_nothing_else(sandbox: DockerSandbox) -> None:
    async with sandbox.session(PROBE) as session:
        await session.start_mocks({"stripe": await SPECS.mock_spec("stripe")})
        await session.wait_mocks(90)
        mock = await session.exec(
            ["python", "-c", CONNECT, "stripe-mock", "4010"], timeout_s=30, mocks=True
        )
        internet = await session.exec(
            ["python", "-c", CONNECT, "1.1.1.1", "53"], timeout_s=30, mocks=True
        )
    assert mock.exit_code == 0, mock.stderr
    assert internet.exit_code != 0


async def test_checks_cannot_change_the_workspace_or_the_image(sandbox: DockerSandbox) -> None:
    async with sandbox.session(PROBE) as session:
        work = await session.exec(["sh", "-c", "echo x > /work/probe.py"], timeout_s=30)
        root = await session.exec(["sh", "-c", "echo x > /opt/tools/bin/ruff"], timeout_s=30)
        tmp = await session.exec(["sh", "-c", "echo x > /tmp/ok && whoami"], timeout_s=30)
    assert work.exit_code != 0 and "Read-only file system" in work.stderr
    assert root.exit_code != 0
    assert tmp.exit_code == 0 and tmp.stdout.strip() == "sandbox"


async def test_a_timeout_kills_the_container_and_nothing_is_left(sandbox: DockerSandbox) -> None:
    async with sandbox.session(PROBE) as session:
        r = await session.exec(["sleep", "60"], timeout_s=2)
        assert r.timed_out
        assert r.duration_ms < 30_000
    ps = await docker("ps", "-aq", "--filter", "label=aidev.validation")
    vols = await docker("volume", "ls", "-q", "--filter", "label=aidev.validation")
    nets = await docker("network", "ls", "-q", "--filter", "label=aidev.validation")
    assert ps.stdout.strip() == vols.stdout.strip() == nets.stdout.strip() == ""


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_seeded_bugs_fail_exactly_the_expected_checks(sandbox: DockerSandbox, case) -> None:
    report = await Validator(sandbox, SPECS).validate(Workspace(apply(dict(BASE), case)))
    assert frozenset(report.failed) == case.expect, [r.summary for r in report.results]

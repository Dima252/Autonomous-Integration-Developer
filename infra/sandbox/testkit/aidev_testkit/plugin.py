"""The pytest plugin the sandbox loads (`-p aidev_testkit.plugin`).

The validator passes the mocks in `AIDEV_MOCKS` (JSON: provider -> upstream URL, the
env variable the code reads, the URL suffix). Proxies start before any test module is
imported, and the code's base-URL variables point at them, so module-level clients
pick them up."""

import json
import os
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

from aidev_testkit.mocks import MockApi, MockApis, serve

_apis = MockApis({})
_servers: list[ThreadingHTTPServer] = []


def pytest_configure(config: pytest.Config) -> None:
    global _apis
    apis = {}
    for name, mock in json.loads(os.environ.get("AIDEV_MOCKS") or "{}").items():
        api = MockApi(name, mock["upstream"], form_only=mock.get("form_only", False))
        server = serve(api)
        _servers.append(server)
        host, port = server.server_address[:2]
        os.environ[mock["env"]] = f"http://{host}:{port}{mock.get('suffix', '')}"
        apis[name] = api
    _apis = MockApis(apis)


def pytest_unconfigure(config: pytest.Config) -> None:
    for server in _servers:
        server.shutdown()


@pytest.fixture
def mock_api() -> MockApis:
    return _apis


@pytest.fixture(autouse=True)
def _api_contract() -> Iterator[None]:
    """Each test starts with no responses set and no calls recorded, and fails if the
    code made a call that the provider's spec doesn't allow."""
    for api in _apis.all():
        api.reset()
    yield
    violations = [v for api in _apis.all() for v in api.violations]
    if violations:
        pytest.fail("\n".join(violations), pytrace=False)

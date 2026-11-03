"""Helpers for tests that run in the validation sandbox (ADR 019).

- Webhook signing the way each provider does it, so signature checks are really tested.
- `mock_api`, a pytest fixture: every call the code makes to a provider goes through a
  local proxy to a Prism mock built from the provider's OpenAPI spec. A call the spec
  doesn't allow fails the test. Tests choose the responses they depend on, and can
  assert on the calls that were made.

    def test_vip_alert(mock_api):
        mock_api.stripe.respond("GET", "/v1/customers/cus_1", {"id": "cus_1", ...})
        ...
        [call] = mock_api.slack.calls_to("POST", "/chat.postMessage")
        assert call.data["channel"] == "C123"
"""

from aidev_testkit.mocks import Call, MockApi, MockApis
from aidev_testkit.signing import sign_github, sign_slack, sign_stripe, stripe_event

__all__ = [
    "Call",
    "MockApi",
    "MockApis",
    "sign_github",
    "sign_slack",
    "sign_stripe",
    "stripe_event",
]

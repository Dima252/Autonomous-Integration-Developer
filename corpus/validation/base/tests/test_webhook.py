import pytest
from aidev_testkit import Call, MockApis, sign_stripe, stripe_event
from fastapi.testclient import TestClient

import app.main as main
import app.slack as slack

SECRET = "whsec_test"


def invoice(customer: str) -> dict[str, object]:
    return {"object": "invoice", "customer": customer, "amount_due": 4900, "currency": "usd"}


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch, mock_api: MockApis) -> None:
    monkeypatch.setattr(main, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(main, "seen_events", set())
    monkeypatch.setattr(main.stripe, "api_key", "sk_test_123")
    monkeypatch.setattr(slack, "TOKEN", "xoxb-test")
    monkeypatch.setattr(slack, "CHANNEL", "C0ALERTS")
    for customer, name, vip in (("cus_vip", "Ada Lovelace", "true"), ("cus_regular", "Bob", "")):
        mock_api.stripe.respond(
            "GET",
            f"/v1/customers/{customer}",
            {"id": customer, "object": "customer", "name": name, "metadata": {"vip": vip}},
        )


def post(payload: bytes, secret: str = SECRET):
    headers = {"stripe-signature": sign_stripe(payload, secret)}
    return TestClient(main.app).post("/webhooks/stripe", content=payload, headers=headers)


def alerts(mock_api: MockApis) -> list[Call]:
    return mock_api.slack.calls_to("POST", "/chat.postMessage")


def test_valid_signature_is_accepted() -> None:
    assert post(stripe_event("invoice.paid", invoice("cus_regular"))).status_code == 200


def test_bad_signature_is_rejected(mock_api: MockApis) -> None:
    payload = stripe_event("invoice.payment_failed", invoice("cus_vip"))
    assert post(payload, secret="whsec_wrong").status_code == 400
    assert alerts(mock_api) == []


def test_vip_payment_failure_alerts_slack(mock_api: MockApis) -> None:
    post(stripe_event("invoice.payment_failed", invoice("cus_vip")))
    [alert] = alerts(mock_api)
    assert alert.data["channel"] == "C0ALERTS"
    assert "Ada Lovelace" in alert.data["text"]
    assert "invoice.payment_failed" in alert.data["text"]
    assert "49.00 USD" in alert.data["text"]


def test_other_customers_are_ignored(mock_api: MockApis) -> None:
    post(stripe_event("invoice.payment_failed", invoice("cus_regular")))
    assert alerts(mock_api) == []


def test_duplicate_deliveries_alert_once(mock_api: MockApis) -> None:
    payload = stripe_event("invoice.payment_failed", invoice("cus_vip"), event_id="evt_dup")
    post(payload)
    post(payload)
    assert len(alerts(mock_api)) == 1


def test_unrelated_events_are_acknowledged(mock_api: MockApis) -> None:
    assert post(stripe_event("customer.created", {"object": "customer"})).status_code == 200
    assert alerts(mock_api) == []

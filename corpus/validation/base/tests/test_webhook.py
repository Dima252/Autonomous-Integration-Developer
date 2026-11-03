import hashlib
import hmac
import json
import time
from typing import Any

import pytest
import stripe
from fastapi.testclient import TestClient

import app.main as main

SECRET = "whsec_test"


def sign(payload: bytes, secret: str = SECRET) -> str:
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256)
    return f"t={ts},v1={mac.hexdigest()}"


def event(kind: str, customer: str = "cus_vip", event_id: str = "evt_1") -> bytes:
    invoice = {"object": "invoice", "customer": customer, "amount_due": 4900, "currency": "usd"}
    body = {"id": event_id, "object": "event", "type": kind, "data": {"object": invoice}}
    return json.dumps(body, separators=(",", ":")).encode()


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(main, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(main, "seen_events", set())

    def retrieve(customer_id: str) -> stripe.Customer:
        vip = "true" if customer_id == "cus_vip" else "false"
        return stripe.Customer.construct_from(
            {"id": customer_id, "name": "Ada", "metadata": {"vip": vip}}, "sk_test"
        )

    def post_alert(event_type: str, customer: Any, invoice: Any) -> None:
        sent.append((event_type, customer.id))

    monkeypatch.setattr(main.stripe.Customer, "retrieve", retrieve)
    monkeypatch.setattr(main, "post_alert", post_alert)
    return sent


def post(payload: bytes, signature: str | None = None):
    client = TestClient(main.app)
    headers = {"stripe-signature": signature if signature is not None else sign(payload)}
    return client.post("/webhooks/stripe", content=payload, headers=headers)


def test_valid_signature_is_accepted(alerts: list[tuple[str, str]]) -> None:
    assert post(event("invoice.paid")).status_code == 200


def test_bad_signature_is_rejected(alerts: list[tuple[str, str]]) -> None:
    payload = event("invoice.paid")
    assert post(payload, sign(payload, "whsec_wrong")).status_code == 400
    assert alerts == []


def test_vip_payment_failure_alerts_slack(alerts: list[tuple[str, str]]) -> None:
    post(event("invoice.payment_failed"))
    assert alerts == [("invoice.payment_failed", "cus_vip")]


def test_other_customers_are_ignored(alerts: list[tuple[str, str]]) -> None:
    post(event("invoice.payment_failed", customer="cus_regular"))
    assert alerts == []


def test_duplicate_deliveries_alert_once(alerts: list[tuple[str, str]]) -> None:
    payload = event("invoice.payment_failed")
    post(payload)
    post(payload)
    assert len(alerts) == 1


def test_unrelated_events_are_acknowledged(alerts: list[tuple[str, str]]) -> None:
    assert post(event("customer.created")).status_code == 200
    assert alerts == []

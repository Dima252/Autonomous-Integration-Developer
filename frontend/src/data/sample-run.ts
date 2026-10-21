// A scripted sample run, played by the replay mode. It shows what the agent graph
// (Phase 3-4) will stream, using exactly the event contract in docs/ui-events.md.
// It is labeled as scripted in the UI: no agent produced it.

import type { RouteInfo, RunEventType } from "@/lib/types";

export interface ScriptedEvent {
  t: number; // ms from the start of the replay
  type: RunEventType;
  label: string;
  node?: string;
  data?: Record<string, unknown>;
}

export interface ScriptedMessage {
  t: number;
  role: "user" | "assistant";
  content: string;
  route?: RouteInfo;
  costUsd?: number;
  latencyMs?: number;
  run?: boolean; // links the run
}

export interface SampleRun {
  id: string;
  title: string;
  request: string;
  messages: ScriptedMessage[];
  events: ScriptedEvent[];
}

const REQUEST =
  "Build a webhook that listens to Stripe billing events and alerts Slack for VIP customers";

const TESTS = `import hashlib
import hmac
import json
import time

import pytest
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


class Customer:
    def __init__(self, cid: str) -> None:
        self.id = cid
        self.name = "Acme Corp"
        self.metadata = {"vip": "true"} if cid == "cus_vip" else {}


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(main, "WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(main, "fetch_customer", Customer)
    monkeypatch.setattr(main, "post_alert", lambda kind, customer, invoice: sent.append(kind))
    main.seen_events.clear()
    return sent


client = TestClient(main.app)


def post(payload: bytes, signature: str | None = None):
    headers = {"stripe-signature": signature or sign(payload)}
    return client.post("/webhooks/stripe", content=payload, headers=headers)


def test_valid_signature_is_accepted(alerts: list[str]) -> None:
    assert post(event("invoice.paid")).status_code == 200


def test_bad_signature_is_rejected(alerts: list[str]) -> None:
    payload = event("invoice.paid")
    assert post(payload, sign(payload, "whsec_wrong")).status_code == 400
    assert alerts == []


def test_vip_payment_failure_alerts_slack(alerts: list[str]) -> None:
    post(event("invoice.payment_failed"))
    assert alerts == ["invoice.payment_failed"]


def test_other_customers_are_ignored(alerts: list[str]) -> None:
    post(event("invoice.payment_failed", customer="cus_regular"))
    assert alerts == []


def test_duplicate_deliveries_alert_once(alerts: list[str]) -> None:
    payload = event("invoice.payment_failed")
    post(payload)
    post(payload)
    assert alerts == ["invoice.payment_failed"]


def test_unrelated_events_are_acknowledged(alerts: list[str]) -> None:
    assert post(event("customer.created")).status_code == 200
    assert alerts == []
`;

const MAIN_V1 = `import json
import os

import stripe
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from app.slack import post_alert

app = FastAPI()
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
stripe.api_key = os.environ.get("STRIPE_API_KEY", "")

BILLING_EVENTS = {"invoice.payment_failed", "invoice.paid", "customer.subscription.deleted"}
# Stripe delivers at least once; remember what we've handled (Redis in production).
seen_events: set[str] = set()


def fetch_customer(customer_id: str) -> stripe.Customer:
    return stripe.Customer.retrieve(customer_id)


def handle(event: stripe.Event) -> None:
    invoice = event["data"]["object"]
    customer = fetch_customer(invoice["customer"])
    if customer.metadata.get("vip") == "true":
        post_alert(event["type"], customer, invoice)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
    body = await request.json()
    payload = json.dumps(body)
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="invalid signature") from None

    if event["id"] in seen_events:
        return {"status": "duplicate"}
    seen_events.add(event["id"])
    if event["type"] in BILLING_EVENTS:
        # Acknowledge fast; Stripe retries slow endpoints.
        background.add_task(handle, event)
    return {"status": "ok"}
`;

const MAIN_V3 = MAIN_V1.replace("import json\nimport os", "import os").replace(
  `    body = await request.json()
    payload = json.dumps(body)
`,
  `    # Stripe signs the exact bytes it sent: verify before parsing anything.
    payload = await request.body()
`,
);

const SLACK = `import os
from typing import Any

import httpx

SLACK_API = "https://slack.com/api/chat.postMessage"
TITLES = {
    "invoice.payment_failed": ":rotating_light: Payment failed",
    "invoice.paid": ":white_check_mark: Invoice paid",
    "customer.subscription.deleted": ":warning: Subscription cancelled",
}


def post_alert(event_type: str, customer: Any, invoice: dict[str, Any]) -> None:
    amount = invoice.get("amount_due", 0) / 100
    currency = str(invoice.get("currency", "")).upper()
    text = f"{TITLES.get(event_type, event_type)} for VIP {customer.name or customer.id}: {amount:.2f} {currency}"
    resp = httpx.post(
        SLACK_API,
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
        json={
            "channel": os.environ["SLACK_CHANNEL_ID"],
            "text": text,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    if not resp.json().get("ok"):
        raise RuntimeError(f"Slack rejected the message: {resp.json().get('error')}")
`;

const REQS_V1 = `fastapi>=0.115
httpx>=0.28
uvicorn[standard]>=0.32
`;

const REQS_V2 = `fastapi>=0.115
httpx>=0.28
stripe>=12.0
uvicorn[standard]>=0.32
`;

const README = `# Stripe billing alerts for VIP customers

Receives Stripe billing webhooks and posts a Slack alert when the customer is a VIP
(\`metadata.vip = "true"\` on the Stripe customer).

## Events
- \`invoice.payment_failed\`, \`invoice.paid\`, \`customer.subscription.deleted\`

## Setup
\`\`\`bash
pip install -r requirements.txt
export STRIPE_API_KEY=sk_test_...        # read customers
export STRIPE_WEBHOOK_SECRET=whsec_...   # from the webhook endpoint
export SLACK_BOT_TOKEN=xoxb-...          # scope: chat:write
export SLACK_CHANNEL_ID=C0123456789
uvicorn app.main:app --port 8000
\`\`\`

## Design decisions
- Slack \`chat.postMessage\` instead of an incoming webhook: Block Kit formatting and a
  configurable channel.
- The signature is verified on the raw body, then the endpoint returns 200 and handles
  the event in the background, because Stripe retries slow endpoints.
- Deliveries are deduplicated by \`event.id\` (in memory here; use Redis in production).

## Assumptions (override any of them)
- "Billing events" means the three events above.
- Alerts go to one channel, set by \`SLACK_CHANNEL_ID\`.
`;

const PYRIGHT_OUT = `/workspace/app/main.py
  /workspace/app/main.py:4:8 - error: Import "stripe" could not be resolved (reportMissingImports)
  /workspace/app/main.py:18:38 - error: "stripe" is not defined (reportUndefinedVariable)
2 errors, 0 warnings, 0 informations`;

const PYTEST_OUT = `tests/test_webhook.py::test_valid_signature_is_accepted FAILED
tests/test_webhook.py::test_bad_signature_is_rejected PASSED
tests/test_webhook.py::test_vip_payment_failure_alerts_slack FAILED
tests/test_webhook.py::test_other_customers_are_ignored PASSED
tests/test_webhook.py::test_duplicate_deliveries_alert_once FAILED
tests/test_webhook.py::test_unrelated_events_are_acknowledged FAILED

____________________ test_valid_signature_is_accepted ____________________
>       assert post(event("invoice.paid")).status_code == 200
E       assert 400 == 200
E        +  where 400 = <Response [400 Bad Request]>.status_code

app/main.py:34: in stripe_webhook
    event = stripe.Webhook.construct_event(payload, signature, WEBHOOK_SECRET)
E   stripe.SignatureVerificationError: No signatures found matching the expected
    signature for payload
========== 4 failed, 2 passed in 0.41s ==========`;

const hits = {
  signature: [
    {
      title: "Receive Stripe events in your webhook endpoint",
      url: "https://docs.stripe.com/webhooks#verify-events",
      breadcrumb: "Stripe › Receive Stripe events in your webhook endpoint › Verify webhook signatures",
      provider: "stripe",
      doc_type: "guide",
    },
    {
      title: "Verify webhook signatures before parsing",
      url: null,
      breadcrumb: "Engineering playbook › Verify webhook signatures before parsing › Stripe (SDK)",
      provider: "general",
      doc_type: "playbook",
    },
  ],
  scope: [
    {
      title: "chat.postMessage",
      url: "https://api.slack.com/methods/chat.postMessage",
      breadcrumb: "Slack › chat.postMessage method › Required scopes",
      provider: "slack",
      doc_type: "operation",
    },
  ],
  invoice: [
    {
      title: "invoice.payment_failed",
      url: "https://docs.stripe.com/api/events/types#event_types-invoice.payment_failed",
      breadcrumb: "Stripe › Webhook event › invoice.payment_failed",
      provider: "stripe",
      doc_type: "webhook_event",
    },
    {
      title: "The Invoice object",
      url: "https://docs.stripe.com/api/invoices/object",
      breadcrumb: "Stripe › Schema › invoice › customer",
      provider: "stripe",
      doc_type: "schema",
    },
  ],
  customer: [
    {
      title: "Retrieve a customer",
      url: "https://docs.stripe.com/api/customers/retrieve",
      breadcrumb: "Stripe › GET /v1/customers/{customer}",
      provider: "stripe",
      doc_type: "operation",
    },
  ],
  blocks: [
    {
      title: "Formatting text for app surfaces",
      url: "https://api.slack.com/reference/surfaces/formatting",
      breadcrumb: "Slack › Formatting text for app surfaces › Basic formatting with mrkdwn",
      provider: "slack",
      doc_type: "guide",
    },
  ],
  idempotency: [
    {
      title: "Handle duplicate events",
      url: "https://docs.stripe.com/webhooks#handle-duplicate-events",
      breadcrumb: "Stripe › Receive Stripe events in your webhook endpoint › Handle duplicate events",
      provider: "stripe",
      doc_type: "guide",
    },
    {
      title: "Idempotency",
      url: null,
      breadcrumb: "Engineering playbook › Idempotency › Webhook consumers",
      provider: "general",
      doc_type: "playbook",
    },
  ],
};

const ROUTE: RouteInfo = {
  intent: "integration_task",
  tier: 1,
  confidence: 0.97,
  systems: ["stripe", "slack"],
  reason: "classifier",
  latency_ms: 1240,
  degraded: false,
};

const S = "researcher:stripe";
const K = "researcher:slack";
const check = (iteration: number, name: string, status: string, summary: string, output?: string) => ({
  iteration,
  check: name,
  status,
  summary,
  ...(output ? { output } : {}),
});

export const SAMPLE_RUN: SampleRun = {
  id: "sample-stripe-vip-alerts",
  title: "Stripe billing → Slack VIP alerts",
  request: REQUEST,
  messages: [
    { t: 0, role: "user", content: REQUEST },
    {
      t: 700,
      role: "assistant",
      content:
        "On it. This needs an agent run: Stripe billing events in, Slack alerts out. Follow along on the right.",
      route: ROUTE,
      costUsd: 0.00012,
      latencyMs: 1310,
      run: true,
    },
    {
      t: 4100,
      role: "assistant",
      content:
        "One question before I design anything, because the answer changes the code:\n\n**How do we identify a VIP customer?** A Stripe metadata flag, a HubSpot property, or a spend threshold?",
    },
    {
      t: 7000,
      role: "user",
      content: "Stripe customer metadata: vip = true",
      route: { ...ROUTE, intent: "followup_edit", confidence: 0.94, systems: ["stripe"], latency_ms: 980 },
    },
    {
      t: 40600,
      role: "assistant",
      content:
        "Done: all checks pass after **3 iterations** in 40 s, for $0.041.\n\n" +
        "- **Iteration 1** failed type checking: `stripe` was missing from `requirements.txt`.\n" +
        "- **Iteration 2** failed 4 of 6 tests: the handler re-serialized the JSON body, so Stripe's signature never matched. It now verifies the raw bytes.\n" +
        "- **Iteration 3** passed everything, and the reviewer approved it.\n\n" +
        "The files, decisions and two assumptions you can override are in the workspace. I saved both lessons for future runs.",
      costUsd: 0.0412,
      latencyMs: 40000,
    },
  ],
  events: [
    { t: 0, type: "run.started", label: "Run started", data: { request: REQUEST, systems: ["stripe", "slack"] } },
    { t: 300, type: "step.started", node: "analyst", label: "Reading the request" },
    {
      t: 1800,
      type: "thought.update",
      node: "analyst",
      label: "Trigger: Stripe billing events · Action: Slack alert · Condition: VIP",
    },
    {
      t: 2600,
      type: "assumption.recorded",
      node: "analyst",
      label: "Billing events = invoice.payment_failed, invoice.paid, customer.subscription.deleted",
      data: {
        text: "Billing events = invoice.payment_failed, invoice.paid, customer.subscription.deleted",
        reason: "The usual billing signals. Doesn't change the design, so it's assumed rather than asked.",
        overridable: true,
      },
    },
    {
      t: 3200,
      type: "assumption.recorded",
      node: "analyst",
      label: "Alerts go to one channel, read from SLACK_CHANNEL_ID",
      data: {
        text: "Alerts go to one Slack channel, read from SLACK_CHANNEL_ID",
        reason: "Configurable without a code change.",
        overridable: true,
      },
    },
    {
      t: 3800,
      type: "clarification.requested",
      node: "analyst",
      label: "Asked how to identify a VIP",
      data: {
        questions: ["How do we identify a VIP customer: a Stripe metadata flag, a HubSpot property, or a spend threshold?"],
      },
    },
    { t: 3900, type: "thought.update", node: "analyst", label: "Waiting for your answer" },
    { t: 7600, type: "thought.update", node: "analyst", label: "VIP = Stripe customer metadata vip=true" },
    {
      t: 8000,
      type: "step.completed",
      node: "analyst",
      label: "Spec: 3 events, 1 condition, 1 action",
      data: { cost_usd: 0.0019 },
    },
    { t: 8200, type: "step.started", node: "architect", label: "Designing the integration" },
    {
      t: 9400,
      type: "decision.recorded",
      node: "architect",
      label: "Slack chat.postMessage, not an incoming webhook",
      data: {
        decision: "Slack chat.postMessage instead of an incoming webhook",
        alternatives: ["Incoming webhook"],
        rationale: "The alert needs Block Kit formatting, and the channel must be configurable.",
      },
    },
    {
      t: 10200,
      type: "decision.recorded",
      node: "architect",
      label: "Verify, return 200, process in the background",
      data: {
        decision: "Verify the signature, return 200 immediately, handle the event in the background",
        alternatives: ["Process inline before responding"],
        rationale: "Stripe retries endpoints that respond slowly (playbook: return 2xx fast).",
      },
    },
    {
      t: 10900,
      type: "decision.recorded",
      node: "architect",
      label: "Deduplicate deliveries by event.id",
      data: {
        decision: "Deduplicate deliveries by event.id",
        alternatives: ["No deduplication"],
        rationale: "Stripe delivers at least once; a duplicate would alert twice (playbook: idempotency).",
      },
    },
    {
      t: 11500,
      type: "step.completed",
      node: "architect",
      label: "3 decisions, 7 questions for research",
      data: { cost_usd: 0.0048 },
    },
    { t: 11700, type: "step.started", node: S, label: "5 questions" },
    { t: 11760, type: "step.started", node: K, label: "2 questions" },
    {
      t: 12600,
      type: "retrieval.hits",
      node: S,
      label: "How is a Stripe webhook signature verified?",
      data: { query: "How is a Stripe webhook signature verified?", hits: hits.signature },
    },
    {
      t: 13300,
      type: "retrieval.hits",
      node: K,
      label: "Which scope does chat.postMessage need?",
      data: { query: "Which OAuth scope does Slack chat.postMessage need?", hits: hits.scope },
    },
    {
      t: 14000,
      type: "retrieval.hits",
      node: S,
      label: "Where is the customer on invoice events?",
      data: { query: "Which field of an invoice event identifies the customer?", hits: hits.invoice },
    },
    {
      t: 14500,
      type: "retrieval.hits",
      node: K,
      label: "How should the alert be formatted?",
      data: { query: "How do I format a Slack message with mrkdwn blocks?", hits: hits.blocks },
    },
    { t: 14700, type: "step.completed", node: K, label: "2 of 2 answered", data: { cost_usd: 0.0071 } },
    {
      t: 15200,
      type: "retrieval.hits",
      node: S,
      label: "How do I read a customer's metadata?",
      data: { query: "How do I retrieve a Stripe customer and its metadata?", hits: hits.customer },
    },
    {
      t: 15700,
      type: "retrieval.hits",
      node: S,
      label: "How do I handle duplicate deliveries?",
      data: { query: "Can Stripe deliver the same event twice, and how should I dedupe?", hits: hits.idempotency },
    },
    { t: 16000, type: "step.completed", node: S, label: "5 of 5 answered", data: { cost_usd: 0.0104 } },
    { t: 16200, type: "step.started", node: "contract", label: "Checking calls against the API catalog" },
    {
      t: 16900,
      type: "thought.update",
      node: "contract",
      label: "GET /v1/customers/{customer} ✓ · chat.postMessage ✓ · 3 events ✓",
    },
    {
      t: 17400,
      type: "step.completed",
      node: "contract",
      label: "2 operations, 3 events verified",
      data: { cost_usd: 0.0121 },
    },
    { t: 17600, type: "step.started", node: "test_author", label: "Writing acceptance tests from the spec" },
    {
      t: 19100,
      type: "code.delta",
      node: "test_author",
      label: "tests/test_webhook.py",
      data: { iteration: 1, path: "tests/test_webhook.py", content: TESTS },
    },
    {
      t: 19400,
      type: "step.completed",
      node: "test_author",
      label: "6 tests written and locked",
      data: { cost_usd: 0.0163 },
    },
    { t: 19600, type: "iteration.started", label: "Iteration 1: first attempt", data: { iteration: 1, reason: "First attempt" } },
    { t: 19700, type: "step.started", node: "coder", label: "Writing app/main.py" },
    {
      t: 20500,
      type: "code.delta",
      node: "coder",
      label: "app/main.py",
      data: { iteration: 1, path: "app/main.py", content: MAIN_V1 },
    },
    {
      t: 20900,
      type: "code.delta",
      node: "coder",
      label: "app/slack.py",
      data: { iteration: 1, path: "app/slack.py", content: SLACK },
    },
    {
      t: 21200,
      type: "code.delta",
      node: "coder",
      label: "requirements.txt",
      data: { iteration: 1, path: "requirements.txt", content: REQS_V1 },
    },
    { t: 21400, type: "step.completed", node: "coder", label: "3 files written", data: { cost_usd: 0.0215 } },
    { t: 21600, type: "step.started", node: "validator", label: "Running the checks" },
    { t: 22100, type: "check.result", node: "validator", label: "install passed", data: check(1, "install", "pass", "3 packages installed") },
    { t: 22500, type: "check.result", node: "validator", label: "ruff passed", data: check(1, "ruff", "pass", "No lint errors") },
    {
      t: 23300,
      type: "check.result",
      node: "validator",
      label: "pyright failed",
      data: check(1, "pyright", "fail", 'Import "stripe" could not be resolved', PYRIGHT_OUT),
    },
    { t: 23350, type: "check.result", node: "validator", label: "grounding skipped", data: check(1, "grounding", "skip", "Skipped after a failure") },
    { t: 23400, type: "check.result", node: "validator", label: "pytest skipped", data: check(1, "pytest", "skip", "Skipped after a failure") },
    { t: 23450, type: "check.result", node: "validator", label: "security skipped", data: check(1, "security", "skip", "Skipped after a failure") },
    { t: 23500, type: "step.completed", node: "validator", label: "Types failed", data: { ok: false } },
    { t: 23700, type: "step.started", node: "diagnostician", label: "Diagnosing the type errors" },
    {
      t: 24600,
      type: "thought.update",
      node: "diagnostician",
      label: "Hypothesis: the stripe SDK isn't in requirements.txt",
    },
    {
      t: 25200,
      type: "step.completed",
      node: "diagnostician",
      label: "Root cause: missing dependency → fix",
      data: { route: "fix", cost_usd: 0.0242 },
    },
    {
      t: 25300,
      type: "iteration.started",
      label: "Caught missing dependency `stripe`, rewriting…",
      data: { iteration: 2, reason: "Caught missing dependency `stripe`" },
    },
    { t: 25400, type: "step.started", node: "coder", label: "Patching requirements.txt" },
    {
      t: 26000,
      type: "code.delta",
      node: "coder",
      label: "requirements.txt",
      data: { iteration: 2, path: "requirements.txt", content: REQS_V2 },
    },
    { t: 26200, type: "step.completed", node: "coder", label: "1 file changed", data: { cost_usd: 0.0261 } },
    { t: 26300, type: "step.started", node: "validator", label: "Running the checks" },
    { t: 26700, type: "check.result", node: "validator", label: "install passed", data: check(2, "install", "pass", "4 packages installed") },
    { t: 27000, type: "check.result", node: "validator", label: "ruff passed", data: check(2, "ruff", "pass", "No lint errors") },
    { t: 27700, type: "check.result", node: "validator", label: "pyright passed", data: check(2, "pyright", "pass", "0 errors") },
    {
      t: 28200,
      type: "check.result",
      node: "validator",
      label: "grounding passed",
      data: check(2, "grounding", "pass", "2 HTTP calls, both in the catalog"),
    },
    {
      t: 29500,
      type: "check.result",
      node: "validator",
      label: "pytest failed",
      data: check(2, "pytest", "fail", "4 failed, 2 passed", PYTEST_OUT),
    },
    { t: 29550, type: "check.result", node: "validator", label: "security skipped", data: check(2, "security", "skip", "Skipped after a failure") },
    { t: 29600, type: "step.completed", node: "validator", label: "4 tests failed", data: { ok: false } },
    { t: 29800, type: "step.started", node: "diagnostician", label: "Diagnosing 4 failing tests" },
    {
      t: 30600,
      type: "thought.update",
      node: "diagnostician",
      label: "Valid signatures are rejected, so the signed bytes changed",
    },
    {
      t: 31200,
      type: "thought.update",
      node: "diagnostician",
      label: "json.dumps(await request.json()) rewrites the body Stripe signed",
    },
    {
      t: 31600,
      type: "step.completed",
      node: "diagnostician",
      label: "Root cause: signature checked on re-serialized JSON → fix",
      data: { route: "fix", cost_usd: 0.0297 },
    },
    {
      t: 31700,
      type: "iteration.started",
      label: "Verify the signature on the raw request body",
      data: { iteration: 3, reason: "Signature must be checked on the raw body" },
    },
    { t: 31800, type: "step.started", node: "coder", label: "Patching app/main.py" },
    {
      t: 32500,
      type: "code.delta",
      node: "coder",
      label: "app/main.py",
      data: { iteration: 3, path: "app/main.py", content: MAIN_V3 },
    },
    { t: 32700, type: "step.completed", node: "coder", label: "1 file changed", data: { cost_usd: 0.0322 } },
    { t: 32800, type: "step.started", node: "validator", label: "Running the checks" },
    { t: 33200, type: "check.result", node: "validator", label: "install passed", data: check(3, "install", "pass", "4 packages installed") },
    { t: 33500, type: "check.result", node: "validator", label: "ruff passed", data: check(3, "ruff", "pass", "No lint errors") },
    { t: 34100, type: "check.result", node: "validator", label: "pyright passed", data: check(3, "pyright", "pass", "0 errors") },
    {
      t: 34500,
      type: "check.result",
      node: "validator",
      label: "grounding passed",
      data: check(3, "grounding", "pass", "2 HTTP calls, both in the catalog"),
    },
    { t: 35300, type: "check.result", node: "validator", label: "pytest passed", data: check(3, "pytest", "pass", "6 passed in 0.38s") },
    {
      t: 35800,
      type: "check.result",
      node: "validator",
      label: "security passed",
      data: check(3, "security", "pass", "No findings; no hardcoded secrets"),
    },
    { t: 35900, type: "step.completed", node: "validator", label: "All checks passed", data: { ok: true } },
    { t: 36100, type: "step.started", node: "reviewer", label: "Reviewing against the spec and playbook" },
    {
      t: 37300,
      type: "check.result",
      node: "reviewer",
      label: "review passed",
      data: check(3, "review", "pass", "Meets the spec; signature, fast-ack and idempotency rules followed"),
    },
    { t: 37400, type: "step.completed", node: "reviewer", label: "Approved", data: { cost_usd: 0.0371 } },
    { t: 37600, type: "step.started", node: "packager", label: "Writing the README" },
    {
      t: 38400,
      type: "code.delta",
      node: "packager",
      label: "README.md",
      data: { iteration: 3, path: "README.md", content: README },
    },
    { t: 38600, type: "step.completed", node: "packager", label: "README, setup and decisions", data: { cost_usd: 0.0398 } },
    { t: 38800, type: "step.started", node: "memory_curator", label: "Saving what this run learned" },
    {
      t: 39300,
      type: "lesson.learned",
      node: "memory_curator",
      label: "Stripe: verify signatures on the raw body",
      data: { lesson: "Stripe: verify webhook signatures on the raw request body; never parse and re-serialize first.", provider: "stripe" },
    },
    {
      t: 39600,
      type: "lesson.learned",
      node: "memory_curator",
      label: "Add every imported SDK to requirements",
      data: { lesson: "Add every imported SDK to requirements.txt before validating.", provider: "general" },
    },
    { t: 39800, type: "step.completed", node: "memory_curator", label: "2 lessons saved", data: { cost_usd: 0.0412 } },
    {
      t: 40000,
      type: "run.completed",
      label: "All checks pass after 3 iterations",
      data: { summary: "All checks pass after 3 iterations", iterations: 3, cost_usd: 0.0412, duration_s: 40 },
    },
  ],
};

"""Render catalog entries as searchable documents.

The catalog answers exact questions ("does POST /v1/customers exist?"); these documents
let fuzzy questions ("how do I create a customer?") find the right entry. Each entry is
one compact chunk: enough to recognize the match, with full detail one `get_operation`
call away.
"""

import html
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from aidev.catalog.models import JsonSchema, Operation, ParsedSpec, SchemaDef, WebhookEvent
from aidev.catalog.openapi import expand_refs, schema_ref_name
from aidev.rag.chunking import BREADCRUMB_SEP, Chunk
from aidev.rag.documents import PROVIDER_NAMES, SourceDoc, doc_key

MAX_CHARS = 5000  # ~1,250 tokens: long enough for big Stripe operations' main fields
DESCRIPTION_CHARS = 600
FIELD_CHARS = 140
MAX_FIELDS = 40
MIN_SCHEMA_PROPERTIES = 3

_TAG = re.compile(r"<[^>]+>")
_CODE = re.compile(r"</?code>")


def catalog_documents(provider: str, spec: ParsedSpec) -> list[SourceDoc]:
    schemas = {s.name: s.json_schema for s in spec.schemas}
    root = PROVIDER_NAMES.get(provider, provider)
    docs = [_operation_doc(provider, root, op, schemas) for op in spec.operations]
    docs += [_event_doc(provider, root, e, schemas) for e in spec.webhook_events]
    docs += [_schema_doc(provider, root, s) for s in spec.schemas if _is_resource(s)]
    return docs


def _operation_doc(
    provider: str, root: str, op: Operation, schemas: Mapping[str, JsonSchema]
) -> SourceDoc:
    title = f"{op.method} {op.path}"
    lines = [f"# {title}", f"Operation ID: `{op.op_id}`"]
    if op.summary:
        lines.append(f"Summary: {_plain(op.summary)}")
    if op.deprecated:
        lines.append("Deprecated: yes")
    if op.scopes:
        lines.append(f"Required scopes: {', '.join(op.scopes)}")
    if op.description:
        lines.append(_clip(_plain(op.description), DESCRIPTION_CHARS))
    if op.parameters:
        lines.append("## Parameters")
        lines += [
            _field(p.name, p.json_schema, p.required, p.description, p.location)
            for p in op.parameters[:MAX_FIELDS]
        ]
    if op.request_body:
        lines.append(f"## Request body ({op.request_body.content_type})")
        lines += _fields(op.request_body.json_schema, schemas)
    if op.responses:
        lines.append("## Responses")
        for r in op.responses:
            ref = schema_ref_name((r.json_schema or {}).get("$ref", ""))
            desc = _plain(r.description or "")
            lines.append(f"- {r.status}: {desc}{f' → `{ref}`' if ref else ''}".rstrip(": "))
    return _doc(provider, root, "operation", op.op_id, title, lines, op.docs_url, "API")


def _event_doc(
    provider: str, root: str, event: WebhookEvent, schemas: Mapping[str, JsonSchema]
) -> SourceDoc:
    lines = [f"# Webhook event `{event.name}`"]
    if event.summary:
        lines.append(_clip(_plain(event.summary), DESCRIPTION_CHARS))
    if event.description:
        lines.append(_clip(_plain(event.description), DESCRIPTION_CHARS))
    if event.payload_schema:
        lines.append("## Payload")
        lines += _fields(event.payload_schema, schemas)
    else:
        lines.append("The payload format isn't in the API spec; see the provider's guides.")
    return _doc(
        provider,
        root,
        "webhook_event",
        event.name,
        event.name,
        lines,
        event.docs_url,
        "Webhook events",
    )


def _schema_doc(provider: str, root: str, schema: SchemaDef) -> SourceDoc:
    lines = [f"# Object `{schema.name}`"]
    if schema.description:
        lines.append(_clip(_plain(schema.description), DESCRIPTION_CHARS))
    lines.append("## Fields")
    lines += _fields(schema.json_schema, {})
    return _doc(provider, root, "schema", schema.name, schema.name, lines, None, "Objects")


def _doc(
    provider: str,
    root: str,
    doc_type: Any,
    name: str,
    title: str,
    lines: list[str],
    url: str | None,
    section: str,
) -> SourceDoc:
    text = _clip("\n".join(lines), MAX_CHARS)
    crumb = BREADCRUMB_SEP.join([root, section, title])
    return SourceDoc(
        key=doc_key(provider, doc_type, name),
        provider=provider,
        doc_type=doc_type,
        title=title,
        url=url,
        chunks=[Chunk(breadcrumb=crumb, text=text)],
    )


def _is_resource(schema: SchemaDef) -> bool:
    """Index objects an integration actually handles, not every nested helper type.
    Stripe marks its API resources; elsewhere, require a real object."""
    s = schema.json_schema
    if "x-stripeResource" in s or "x-resourceId" in s:
        return True
    if schema.name.startswith("webhook"):  # GitHub payload fragments
        return False
    return len(s.get("properties") or {}) >= MIN_SCHEMA_PROPERTIES


def _fields(schema: JsonSchema, schemas: Mapping[str, JsonSchema]) -> list[str]:
    """Top-level fields of a schema, following one level of `$ref` (and `allOf`)."""
    resolved = expand_refs(schema, schemas, depth=1)
    props: dict[str, Any] = {}
    required: set[str] = set()
    for part in [resolved, *resolved.get("allOf", [])]:
        props.update(part.get("properties") or {})
        required.update(part.get("required") or [])
    if not props:
        ref = schema_ref_name(resolved.get("$ref", ""))
        return [f"`{ref}` object"] if ref else []
    # Required first, then alphabetical (JSONB doesn't keep the spec's key order).
    ordered = sorted(props.items(), key=lambda kv: (kv[0] not in required, kv[0]))
    fields = [
        _field(name, prop, name in required, prop.get("description"))
        for name, prop in ordered[:MAX_FIELDS]
    ]
    if len(props) > MAX_FIELDS:
        fields.append(f"- … {len(props) - MAX_FIELDS} more fields")
    return fields


def _field(
    name: str,
    schema: JsonSchema,
    required: bool,
    description: str | None,
    location: str | None = None,
) -> str:
    attrs = [a for a in (location, _type(schema), "required" if required else None) if a]
    desc = _clip(_plain(description or schema.get("description") or ""), FIELD_CHARS)
    return f"- `{name}` ({', '.join(attrs)}){': ' + desc if desc else ''}"


def _type(schema: JsonSchema) -> str:
    if ref := schema_ref_name(schema.get("$ref", "")):
        return ref
    if "enum" in schema and len(schema["enum"]) <= 6:
        return " | ".join(f"`{json.dumps(v)}`" for v in schema["enum"])
    kind = schema.get("type")
    if kind == "array":
        return f"array of {_type(schema.get('items') or {})}"
    if isinstance(kind, list):
        return " | ".join(str(k) for k in kind)
    for combo in ("anyOf", "oneOf"):
        if combo in schema:
            return " | ".join(_type(s) for s in _first(schema[combo], 3))
    return str(kind or "object")


def _first(items: Iterable[Any], n: int) -> list[Any]:
    return list(items)[:n]


def _plain(text: str) -> str:
    """Spec descriptions mix Markdown and HTML (Stripe uses `<p>`, `<code>`)."""
    text = _CODE.sub("`", text)
    text = _TAG.sub("", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

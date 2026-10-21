"""Parse an OpenAPI 3.x or Swagger 2.0 document into catalog models.

Named schemas stay as `$ref`s (normalized to `#/components/schemas/<name>`) rather than
being inlined. Real specs are recursive (Stripe's `customer` and `subscription` refer to
each other), so fully dereferencing them blows up. Callers expand references to the
depth they need with `expand_refs`. Every other kind of `$ref` (parameters, request
bodies, responses, path items) is inlined, so an `Operation` is complete on its own.
"""

import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import unquote

from aidev.catalog.models import (
    SCHEMA_REF_PREFIX,
    HttpMethod,
    JsonSchema,
    Operation,
    Parameter,
    ParamLocation,
    ParsedSpec,
    RequestBody,
    Response,
    SchemaDef,
    WebhookEvent,
)
from aidev.catalog.profiles import SpecProfile

METHODS = ("get", "put", "post", "delete", "options", "head", "patch")
_SCHEMA_REF_PREFIXES = ("#/components/schemas/", "#/definitions/")
_MEDIA_PREFERENCE = ("application/json", "application/x-www-form-urlencoded", "multipart/form-data")
_PARAM_META_KEYS = {"name", "in", "description", "required"}
_PARAM_LOCATIONS = {"path", "query", "header", "cookie"}


class SpecError(ValueError):
    """The document is not an OpenAPI / Swagger spec this parser can read."""


def parse_spec(doc: Mapping[str, Any], profile: SpecProfile | None = None) -> ParsedSpec:
    version = str(doc.get("openapi") or doc.get("swagger") or "")
    if not version:
        raise SpecError("missing 'openapi' / 'swagger' version field")
    swagger2 = version.startswith("2")
    parser = _Parser(doc, swagger2, profile or SpecProfile())
    info = doc.get("info") or {}
    return ParsedSpec(
        title=str(info.get("title", "")),
        api_version=str(info.get("version", "")),
        spec_format=f"{'swagger' if swagger2 else 'openapi'} {version}",
        base_urls=parser.base_urls(),
        operations=parser.operations(),
        webhook_events=parser.webhook_events(),
        schemas=parser.schemas(),
    )


def schema_ref_name(ref: str) -> str | None:
    """`#/components/schemas/Foo` or `#/definitions/Foo` -> `Foo`. None for any other ref,
    including pointers *into* a schema (`#/components/schemas/Foo/properties/bar`)."""
    for prefix in _SCHEMA_REF_PREFIXES:
        if ref.startswith(prefix):
            rest = ref[len(prefix) :]
            return None if "/" in rest else _unescape(rest)
    return None


def expand_refs(schema: Any, schemas: Mapping[str, JsonSchema], depth: int = 1) -> Any:
    """Inline named-schema refs up to `depth` levels deep. Deeper refs, refs to unknown
    schemas, and recursive refs are left as `$ref`s."""

    def walk(node: Any, level: int, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(n, level, stack) for n in node]
        if not isinstance(node, dict):
            return node
        name = schema_ref_name(node["$ref"]) if "$ref" in node else None
        if name is not None:
            if level >= depth or name in stack or name not in schemas:
                return node
            return walk(schemas[name], level + 1, (*stack, name))
        return {k: walk(v, level, stack) for k, v in node.items()}

    return walk(schema, 0, ())


def path_regex(path: str) -> str:
    """`/v1/customers/{customer}` -> `^/v1/customers/[^/]+/?$`, for matching concrete
    request paths back to a templated operation."""
    parts = re.split(r"(\{[^}/]+\})", path.rstrip("/"))
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return f"^{body}/?$"


def _unescape(token: str) -> str:
    return unquote(token).replace("~1", "/").replace("~0", "~")


class _Parser:
    def __init__(self, doc: Mapping[str, Any], swagger2: bool, profile: SpecProfile) -> None:
        self.doc = doc
        self.swagger2 = swagger2
        self.profile = profile

    # -- references -------------------------------------------------------------------

    def _lookup(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise SpecError(f"external $ref not supported: {ref}")
        node: Any = self.doc
        for token in ref[2:].split("/"):
            try:
                node = node[_unescape(token)]
            except (KeyError, IndexError, TypeError):
                raise SpecError(f"unresolvable $ref: {ref}") from None
        return node

    def deref(self, node: Any) -> Any:
        """Follow non-schema `$ref`s (parameters, responses, ...) to the concrete object."""
        seen: set[str] = set()
        while isinstance(node, dict) and "$ref" in node and schema_ref_name(node["$ref"]) is None:
            ref = node["$ref"]
            if ref in seen:
                raise SpecError(f"circular $ref: {ref}")
            seen.add(ref)
            node = self._lookup(ref)
        return node

    def normalize(self, node: Any) -> Any:
        """Copy a schema with named-schema refs canonicalized and other refs inlined."""
        if isinstance(node, list):
            return [self.normalize(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = schema_ref_name(node["$ref"])
            if name is None:
                return self.normalize(self.deref(node))
            siblings = {k: self.normalize(v) for k, v in node.items() if k != "$ref"}
            return {"$ref": SCHEMA_REF_PREFIX + name, **siblings}
        return {k: self.normalize(v) for k, v in node.items()}

    # -- top level --------------------------------------------------------------------

    def base_urls(self) -> list[str]:
        if self.swagger2:
            host = self.doc.get("host")
            if not host:
                return []
            base = self.doc.get("basePath", "")
            return [f"{s}://{host}{base}".rstrip("/") for s in self.doc.get("schemes") or ["https"]]
        return [str(s["url"]).rstrip("/") for s in self.doc.get("servers") or [] if s.get("url")]

    def schemas(self) -> list[SchemaDef]:
        if self.swagger2:
            raw = self.doc.get("definitions") or {}
        else:
            raw = (self.doc.get("components") or {}).get("schemas") or {}
        return [
            SchemaDef(
                name=name,
                description=schema.get("description") if isinstance(schema, dict) else None,
                json_schema=self.normalize(schema),
            )
            for name, schema in raw.items()
        ]

    def operations(self) -> list[Operation]:
        ops: list[Operation] = []
        seen: set[str] = set()
        for path, item in (self.doc.get("paths") or {}).items():
            item = self.deref(item)
            shared = item.get("parameters") or []
            for method in METHODS:
                raw = item.get(method)
                if not isinstance(raw, dict):
                    continue
                op = self._operation(path, method, raw, shared)
                if op.op_id in seen:  # duplicate operationIds occur in real specs
                    op.op_id = f"{op.method} {op.path}"
                seen.add(op.op_id)
                ops.append(op)
        return ops

    def webhook_events(self) -> list[WebhookEvent]:
        # OpenAPI 3.1 `webhooks`, or the `x-webhooks` extension 3.0 specs (GitHub) use.
        section = self.doc.get("webhooks") or self.doc.get("x-webhooks") or {}
        events: dict[str, WebhookEvent] = {}
        for key, item in section.items():
            item = self.deref(item)
            for method in METHODS:
                raw = item.get(method)
                if not isinstance(raw, dict):
                    continue
                body = self._request_body(raw["requestBody"]) if "requestBody" in raw else None
                name = self.profile.webhook_name(key, raw)
                events[name] = WebhookEvent(
                    name=name,
                    summary=raw.get("summary"),
                    description=raw.get("description"),
                    payload_schema=body.json_schema if body else None,
                    docs_url=(raw.get("externalDocs") or {}).get("url"),
                )
        for event in self.profile.webhook_events(self.doc):
            if event.name not in events:
                if event.payload_schema is not None:
                    event.payload_schema = self.normalize(event.payload_schema)
                events[event.name] = event
        return list(events.values())

    # -- operation parts --------------------------------------------------------------

    def _operation(
        self, path: str, method: str, raw: Mapping[str, Any], shared: list[Any]
    ) -> Operation:
        merged: dict[tuple[str, str], Mapping[str, Any]] = {}
        for p in [*shared, *(raw.get("parameters") or [])]:
            p = self.deref(p)
            merged[(p["name"], p["in"])] = p  # operation-level wins over path-level

        parameters: list[Parameter] = []
        body: RequestBody | None = None
        form_fields: list[Mapping[str, Any]] = []
        for (name, location), p in merged.items():
            if location == "body":  # Swagger 2
                body = RequestBody(
                    content_type=self._swagger2_media(raw, "consumes"),
                    required=bool(p.get("required")),
                    json_schema=self.normalize(p.get("schema") or {}),
                )
            elif location == "formData":  # Swagger 2
                form_fields.append(p)
            elif location in _PARAM_LOCATIONS:
                parameters.append(
                    Parameter(
                        name=name,
                        location=cast(ParamLocation, location),
                        required=bool(p.get("required")) or location == "path",
                        description=p.get("description"),
                        json_schema=self._param_schema(p),
                    )
                )
        if form_fields:
            body = self._form_body(form_fields)
        if "requestBody" in raw:
            body = self._request_body(raw["requestBody"])

        security = raw.get("security", self.doc.get("security")) or []
        declared = sorted({s for req in security for scopes in req.values() for s in scopes})
        return Operation(
            op_id=str(raw.get("operationId") or f"{method.upper()} {path}"),
            method=cast(HttpMethod, method.upper()),
            path=path,
            summary=raw.get("summary"),
            description=raw.get("description"),
            tags=list(raw.get("tags") or []),
            parameters=parameters,
            request_body=body,
            responses=self._responses(raw),
            scopes=self.profile.scopes(raw, declared),
            deprecated=bool(raw.get("deprecated")),
            docs_url=(raw.get("externalDocs") or {}).get("url"),
        )

    def _param_schema(self, p: Mapping[str, Any]) -> JsonSchema:
        if "schema" in p:
            return self.normalize(p["schema"])
        if "content" in p:  # OpenAPI 3 parameters may use a media type instead
            media = _pick_media(p["content"])
            return self.normalize(media[1].get("schema") or {}) if media else {}
        # Swagger 2 non-body parameters carry their schema keywords inline.
        inline = {k: v for k, v in p.items() if k not in _PARAM_META_KEYS and k[:2] != "x-"}
        return self.normalize(inline)

    def _form_body(self, fields: list[Mapping[str, Any]]) -> RequestBody:
        properties: dict[str, Any] = {}
        for f in fields:
            prop = self._param_schema(f)
            if f.get("description"):
                prop["description"] = f["description"]
            properties[f["name"]] = prop
        required = [f["name"] for f in fields if f.get("required")]
        schema: JsonSchema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        is_upload = any(f.get("type") == "file" for f in fields)
        return RequestBody(
            content_type="multipart/form-data" if is_upload else _MEDIA_PREFERENCE[1],
            required=bool(required),
            json_schema=schema,
        )

    def _request_body(self, rb: Any) -> RequestBody | None:
        rb = self.deref(rb)
        media = _pick_media(rb.get("content") or {})
        if media is None:
            return None
        content_type, obj = media
        return RequestBody(
            content_type=content_type,
            required=bool(rb.get("required")),
            json_schema=self.normalize(obj.get("schema") or {}),
        )

    def _responses(self, raw: Mapping[str, Any]) -> list[Response]:
        responses = []
        for status, r in (raw.get("responses") or {}).items():
            r = self.deref(r)
            if self.swagger2:
                schema = r.get("schema")
                content_type = self._swagger2_media(raw, "produces") if schema else None
            else:
                media = _pick_media(r.get("content") or {})
                content_type = media[0] if media else None
                schema = media[1].get("schema") if media else None
            responses.append(
                Response(
                    status=str(status),
                    description=r.get("description") or None,
                    content_type=content_type,
                    json_schema=self.normalize(schema) if schema is not None else None,
                )
            )
        return responses

    def _swagger2_media(self, raw: Mapping[str, Any], key: str) -> str:
        types = raw.get(key) or self.doc.get(key) or []
        return next((t for t in types if "json" in t), types[0] if types else _MEDIA_PREFERENCE[0])


def _pick_media(content: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    """Choose the media type an integration would use: JSON, then forms, then anything."""
    if not content:
        return None
    for preferred in _MEDIA_PREFERENCE:
        for content_type, obj in content.items():
            if content_type.split(";")[0].strip() == preferred:
                return content_type, obj
    for content_type, obj in content.items():
        if "json" in content_type:
            return content_type, obj
    content_type, obj = next(iter(content.items()))
    return content_type, obj

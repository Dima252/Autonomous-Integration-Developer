from typing import Any, Literal

from pydantic import BaseModel

JsonSchema = dict[str, Any]
HttpMethod = Literal["GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH"]
ParamLocation = Literal["path", "query", "header", "cookie"]

# Schema references are normalized to this form whatever the spec version, so
# `#/definitions/X` (Swagger 2) and `#/components/schemas/X` (OpenAPI 3) look the same.
SCHEMA_REF_PREFIX = "#/components/schemas/"


class Parameter(BaseModel):
    name: str
    location: ParamLocation
    required: bool = False
    description: str | None = None
    json_schema: JsonSchema = {}


class RequestBody(BaseModel):
    content_type: str
    required: bool = False
    json_schema: JsonSchema = {}


class Response(BaseModel):
    status: str  # "200", "4XX", "default"
    description: str | None = None
    content_type: str | None = None
    json_schema: JsonSchema | None = None


class Operation(BaseModel):
    op_id: str
    method: HttpMethod
    path: str
    summary: str | None = None
    description: str | None = None
    tags: list[str] = []
    parameters: list[Parameter] = []
    request_body: RequestBody | None = None
    responses: list[Response] = []
    scopes: list[str] = []
    deprecated: bool = False
    docs_url: str | None = None


class WebhookEvent(BaseModel):
    name: str  # as the provider names it: "invoice.paid", "issues.opened", "contact.creation"
    summary: str | None = None
    description: str | None = None
    payload_schema: JsonSchema | None = None  # None when the spec doesn't describe it
    docs_url: str | None = None


class SchemaDef(BaseModel):
    name: str
    description: str | None = None
    json_schema: JsonSchema


class ParsedSpec(BaseModel):
    title: str
    api_version: str
    spec_format: str  # "openapi 3.0.3", "swagger 2.0"
    base_urls: list[str]
    operations: list[Operation]
    webhook_events: list[WebhookEvent]
    schemas: list[SchemaDef]

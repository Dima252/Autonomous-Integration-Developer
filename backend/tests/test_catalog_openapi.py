import re
from typing import Any

import pytest

from aidev.catalog.openapi import SpecError, expand_refs, parse_spec, path_regex

OPENAPI3: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Shop", "version": "2026-01-01"},
    "servers": [{"url": "https://api.shop.test/"}],
    "security": [{"oauth": ["orders:read"]}],
    "paths": {
        "/orders/{order_id}": {
            "parameters": [{"$ref": "#/components/parameters/OrderId"}],
            "get": {
                "operationId": "getOrder",
                "summary": "Get an order",
                "tags": ["orders"],
                "parameters": [{"name": "expand", "in": "query", "schema": {"type": "string"}}],
                "responses": {
                    "200": {"$ref": "#/components/responses/OrderResponse"},
                    "404": {"description": "Not found"},
                },
                "externalDocs": {"url": "https://docs.shop.test/orders#get"},
            },
            "post": {
                "operationId": "updateOrder",
                "security": [{"oauth": ["orders:write"]}],
                "deprecated": True,
                "requestBody": {"$ref": "#/components/requestBodies/OrderUpdate"},
                "responses": {"200": {"description": "OK"}},
            },
        },
        "/orders": {
            "post": {
                # no operationId: falls back to "METHOD path"
                "requestBody": {
                    "content": {
                        "text/plain": {"schema": {"type": "string"}},
                        "application/json; charset=utf-8": {
                            "schema": {"$ref": "#/components/schemas/Order"}
                        },
                    }
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
    "x-webhooks": {
        "order-paid": {
            "post": {
                "operationId": "order/paid",
                "summary": "An order was paid",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Order"}}
                    }
                },
            }
        }
    },
    "components": {
        "parameters": {
            "OrderId": {"name": "order_id", "in": "path", "schema": {"type": "string"}},
        },
        "requestBodies": {
            "OrderUpdate": {
                "required": True,
                "content": {
                    "application/x-www-form-urlencoded": {
                        "schema": {"properties": {"note": {"type": "string"}}}
                    }
                },
            }
        },
        "responses": {
            "OrderResponse": {
                "description": "The order",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}},
            }
        },
        "schemas": {
            "Order": {
                "description": "A customer order",
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "customer": {"$ref": "#/components/schemas/Customer"},
                    "lines": {"type": "array", "items": {"$ref": "#/components/schemas/Line"}},
                },
            },
            "Customer": {
                "type": "object",
                "properties": {"last_order": {"$ref": "#/components/schemas/Order"}},
            },
            "Line": {"type": "object", "properties": {"sku": {"type": "string"}}},
        },
    },
}

SWAGGER2: dict[str, Any] = {
    "swagger": "2.0",
    "info": {"title": "Chat", "version": "1.0"},
    "host": "chat.test",
    "basePath": "/api",
    "schemes": ["https"],
    "consumes": ["application/x-www-form-urlencoded", "application/json"],
    "produces": ["application/json"],
    "paths": {
        "/messages.post": {
            "post": {
                "operationId": "messages_post",
                "parameters": [
                    {"name": "token", "in": "header", "required": True, "type": "string"},
                    {
                        "name": "channel",
                        "in": "formData",
                        "required": True,
                        "type": "string",
                        "description": "Channel ID",
                    },
                    {"name": "text", "in": "formData", "type": "string"},
                ],
                "responses": {
                    "200": {"description": "OK", "schema": {"$ref": "#/definitions/Msg"}}
                },
            }
        },
        "/files.upload": {
            "post": {
                "operationId": "files_upload",
                "parameters": [{"name": "file", "in": "formData", "type": "file"}],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/messages.import": {
            "post": {
                "operationId": "messages_import",
                "parameters": [
                    {"name": "body", "in": "body", "schema": {"$ref": "#/definitions/Msg"}}
                ],
                "responses": {"200": {"$ref": "#/responses/Ok"}},
            }
        },
    },
    "responses": {"Ok": {"description": "Shared OK", "schema": {"type": "object"}}},
    "definitions": {"Msg": {"type": "object", "properties": {"ts": {"type": "string"}}}},
}


def test_openapi3_operation_is_complete_and_refs_inlined() -> None:
    spec = parse_spec(OPENAPI3)
    ops = {op.op_id: op for op in spec.operations}

    get = ops["getOrder"]
    assert (get.method, get.path, get.tags) == ("GET", "/orders/{order_id}", ["orders"])
    # Path-level parameter (via $ref) merges with the operation's own.
    params = {(p.name, p.location, p.required) for p in get.parameters}
    assert params == {("order_id", "path", True), ("expand", "query", False)}
    # Response $ref inlined; the named schema stays a canonical ref.
    ok = next(r for r in get.responses if r.status == "200")
    assert ok.description == "The order"
    assert ok.json_schema == {"$ref": "#/components/schemas/Order"}
    assert get.scopes == ["orders:read"]  # inherited from the root security
    assert get.docs_url == "https://docs.shop.test/orders#get"


def test_operation_security_overrides_root_and_request_body_ref_is_inlined() -> None:
    update = next(op for op in parse_spec(OPENAPI3).operations if op.op_id == "updateOrder")

    assert update.scopes == ["orders:write"]
    assert update.deprecated
    assert update.request_body is not None
    assert update.request_body.required
    assert update.request_body.content_type == "application/x-www-form-urlencoded"


def test_missing_operation_id_falls_back_and_json_media_is_preferred() -> None:
    create = next(op for op in parse_spec(OPENAPI3).operations if op.path == "/orders")

    assert create.op_id == "POST /orders"
    assert create.request_body is not None
    assert create.request_body.content_type == "application/json; charset=utf-8"


def test_spec_metadata_and_schemas() -> None:
    spec = parse_spec(OPENAPI3)

    assert (spec.title, spec.api_version, spec.spec_format) == (
        "Shop",
        "2026-01-01",
        "openapi 3.0.3",
    )
    assert spec.base_urls == ["https://api.shop.test"]
    schemas = {s.name: s for s in spec.schemas}
    assert set(schemas) == {"Order", "Customer", "Line"}
    assert schemas["Order"].description == "A customer order"


def test_x_webhooks_section_becomes_events() -> None:
    (event,) = parse_spec(OPENAPI3).webhook_events

    assert event.name == "order/paid"
    assert event.summary == "An order was paid"
    assert event.payload_schema == {"$ref": "#/components/schemas/Order"}


def test_swagger2_form_params_become_a_request_body() -> None:
    spec = parse_spec(SWAGGER2)
    ops = {op.op_id: op for op in spec.operations}

    post = ops["messages_post"]
    assert [(p.name, p.location) for p in post.parameters] == [("token", "header")]
    assert post.parameters[0].json_schema == {"type": "string"}
    assert post.request_body is not None
    assert post.request_body.content_type == "application/x-www-form-urlencoded"
    assert post.request_body.json_schema == {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel ID"},
            "text": {"type": "string"},
        },
        "required": ["channel"],
    }
    # Swagger 2 `#/definitions/X` is normalized to the OpenAPI 3 form.
    assert post.responses[0].json_schema == {"$ref": "#/components/schemas/Msg"}
    assert post.responses[0].content_type == "application/json"


def test_swagger2_body_param_file_upload_and_shared_response() -> None:
    ops = {op.op_id: op for op in parse_spec(SWAGGER2).operations}

    imp = ops["messages_import"]
    assert imp.request_body is not None
    assert imp.request_body.content_type == "application/json"
    assert imp.request_body.json_schema == {"$ref": "#/components/schemas/Msg"}
    assert imp.responses[0].description == "Shared OK"

    upload = ops["files_upload"]
    assert upload.request_body is not None
    assert upload.request_body.content_type == "multipart/form-data"


def test_swagger2_metadata() -> None:
    spec = parse_spec(SWAGGER2)

    assert spec.spec_format == "swagger 2.0"
    assert spec.base_urls == ["https://chat.test/api"]
    assert [s.name for s in spec.schemas] == ["Msg"]


def test_duplicate_operation_ids_are_made_unique() -> None:
    doc = {
        "openapi": "3.0.0",
        "paths": {
            "/a": {"get": {"operationId": "dup", "responses": {}}},
            "/b": {"get": {"operationId": "dup", "responses": {}}},
        },
    }

    assert [op.op_id for op in parse_spec(doc).operations] == ["dup", "GET /b"]


@pytest.mark.parametrize(
    "doc",
    [
        {"info": {}},
        {"openapi": "3.0.0", "paths": {"/x": {"get": {"parameters": [{"$ref": "#/nope"}]}}}},
        {"openapi": "3.0.0", "paths": {"/x": {"$ref": "other.yaml#/paths/x"}}},
    ],
    ids=["no-version", "dangling-ref", "external-ref"],
)
def test_invalid_specs_raise_spec_error(doc: dict[str, Any]) -> None:
    with pytest.raises(SpecError):
        parse_spec(doc)


def test_expand_refs_respects_depth_and_stops_on_cycles() -> None:
    schemas = {s.name: s.json_schema for s in parse_spec(OPENAPI3).schemas}
    order = {"$ref": "#/components/schemas/Order"}

    assert expand_refs(order, schemas, depth=0) == order

    one = expand_refs(order, schemas, depth=1)
    assert one["properties"]["customer"] == {"$ref": "#/components/schemas/Customer"}
    assert one["properties"]["lines"]["items"] == {"$ref": "#/components/schemas/Line"}

    deep = expand_refs(order, schemas, depth=10)
    customer = deep["properties"]["customer"]
    # Customer.last_order points back at Order: left as a ref instead of looping.
    assert customer["properties"]["last_order"] == order
    assert deep["properties"]["lines"]["items"]["properties"] == {"sku": {"type": "string"}}


@pytest.mark.parametrize(
    ("template", "path", "matches"),
    [
        ("/v1/customers/{customer}", "/v1/customers/cus_123", True),
        ("/v1/customers/{customer}", "/v1/customers/cus_123/", True),
        ("/v1/customers/{customer}", "/v1/customers", False),
        ("/v1/customers/{customer}", "/v1/customers/a/b", False),
        ("/chat.postMessage", "/chat.postMessage", True),
        ("/chat.postMessage", "/chatXpostMessage", False),  # dots are literal
        ("/repos/{owner}/{repo}", "/repos/octo/hello", True),
    ],
)
def test_path_regex(template: str, path: str, matches: bool) -> None:
    assert bool(re.match(path_regex(template), path)) is matches

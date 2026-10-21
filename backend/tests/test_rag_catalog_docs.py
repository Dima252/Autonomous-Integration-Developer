from aidev.catalog.openapi import parse_spec
from aidev.rag.catalog_docs import catalog_documents
from tests.test_catalog_openapi import OPENAPI3


def test_catalog_entries_become_single_chunk_documents() -> None:
    docs = {d.key: d for d in catalog_documents("shop", parse_spec(OPENAPI3))}

    assert set(docs) == {
        "shop/operation/getOrder",
        "shop/operation/updateOrder",
        "shop/operation/POST /orders",
        "shop/webhook_event/order/paid",
        "shop/schema/Order",
    }
    assert all(len(d.chunks) == 1 for d in docs.values())


def test_operation_document_lists_what_identifies_it() -> None:
    doc = next(
        d
        for d in catalog_documents("shop", parse_spec(OPENAPI3))
        if d.key == "shop/operation/getOrder"
    )
    (chunk,) = doc.chunks

    assert doc.title == "GET /orders/{order_id}"
    assert chunk.breadcrumb == "shop › API › GET /orders/{order_id}"
    assert "Operation ID: `getOrder`" in chunk.text
    assert "Required scopes: orders:read" in chunk.text
    assert "- `order_id` (path, string, required)" in chunk.text
    assert "- 200: The order → `Order`" in chunk.text
    assert doc.url == "https://docs.shop.test/orders#get"


def test_event_payload_fields_follow_one_ref() -> None:
    doc = next(
        d for d in catalog_documents("shop", parse_spec(OPENAPI3)) if d.doc_type == "webhook_event"
    )
    text = doc.chunks[0].text

    assert "- `customer` (Customer)" in text
    assert "- `lines` (array of Line)" in text


def test_only_substantial_schemas_are_indexed() -> None:
    names = {
        d.title for d in catalog_documents("shop", parse_spec(OPENAPI3)) if d.doc_type == "schema"
    }

    # Customer and Line have fewer than three properties.
    assert names == {"Order"}


def test_html_in_descriptions_is_flattened_and_required_fields_come_first() -> None:
    spec = parse_spec(
        {
            "openapi": "3.0.0",
            "paths": {
                "/v1/things": {
                    "post": {
                        "operationId": "PostThings",
                        "description": "<p>Creates a <code>thing</code> &amp; more.</p>",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "required": ["zeta"],
                                        "properties": {
                                            "alpha": {"type": "string"},
                                            "zeta": {"type": "string", "enum": ["", "x"]},
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {},
                    }
                }
            },
        }
    )
    (doc,) = catalog_documents("stripe", spec)
    text = doc.chunks[0].text

    assert "Creates a `thing` & more." in text
    assert text.index("`zeta`") < text.index("`alpha`")
    assert '`""` | `"x"`' in text

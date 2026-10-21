from aidev.rag.chunking import (
    MAX_TOKENS,
    MIN_TOKENS,
    approx_tokens,
    chunk_markdown,
    clean_markdown,
)


def _words(n: int, word: str = "lorem") -> str:
    return " ".join([word] * n)


def test_clean_markdown_strips_site_furniture() -> None:
    raw = (
        "> ## Documentation Index\n"
        "> Fetch the complete documentation index at: https://x.test/llms.txt\n"
        "\n"
        "---\nid: 123\n---\n"
        "\n"
        "Source: https://docs.x.test/page\n"
        "\n"
        "# Verifying requests {#top}\n"
        "\n\n\n"
        "Body text.\r\n"
    )

    assert clean_markdown(raw) == "# Verifying requests\n\nBody text."


def test_clean_markdown_keeps_anchors_outside_headings() -> None:
    assert clean_markdown("Use `{#id}` syntax here.") == "Use `{#id}` syntax here."


def test_breadcrumbs_follow_the_heading_hierarchy() -> None:
    md = "\n\n".join(
        [
            "# Webhooks",
            _words(200),
            "## Verify signatures",
            _words(500),
            "### Replay protection",
            _words(500),
            "## Retries",
            _words(500),
        ]
    )

    crumbs = [c.breadcrumb for c in chunk_markdown(md, "Stripe")]

    assert crumbs == [
        "Stripe › Webhooks",
        "Stripe › Webhooks › Verify signatures",
        "Stripe › Webhooks › Verify signatures › Replay protection",
        "Stripe › Webhooks › Retries",
    ]


def test_hash_lines_inside_code_fences_are_not_headings() -> None:
    md = "# Guide\n\n```ruby\n# Replace this secret\nsecret = 'x'\n```\n\n## Next\n\nText."

    chunks = chunk_markdown(md, "Root")

    assert len(chunks) == 1  # small sections merge
    assert "# Replace this secret" in chunks[0].text
    assert chunks[0].breadcrumb == "Root › Guide"


def test_sdk_language_sections_other_than_python_are_dropped() -> None:
    md = "\n\n".join(
        [
            "# Handler",
            "## Ruby",
            "```ruby\nputs 'ruby'\n```",
            "## Node.js",
            "```js\nconsole.log('node')\n```",
            "## Python",
            "```python\nprint('python')\n```",
        ]
    )

    text = "\n".join(c.text for c in chunk_markdown(md, "Stripe"))

    assert "print('python')" in text
    assert "ruby" not in text.lower()
    assert "node" not in text.lower()


def test_small_sections_merge_and_large_ones_split_within_bounds() -> None:
    sections = [f"## Part {i}\n\n{_words(60)}" for i in range(6)]
    big = "## Big\n\n" + "\n\n".join(_words(150, f"w{i}") for i in range(20))
    md = "# Page\n\n" + "\n\n".join([*sections, big])

    chunks = chunk_markdown(md, "Root")

    assert all(c.tokens <= MAX_TOKENS for c in chunks)
    # Six ~75-token sections don't become six chunks.
    small = [c for c in chunks if "Big" not in c.breadcrumb]
    assert len(small) < 6
    assert sum(1 for c in chunks if c.breadcrumb.endswith("Big")) > 1
    assert all(c.tokens >= MIN_TOKENS for c in chunks[:-1])


def test_oversized_code_block_is_never_split_mid_fence_unless_it_alone_is_too_big() -> None:
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(200)) + "\n```"
    md = "# Page\n\n## Example\n\n" + _words(500) + "\n\n" + code

    chunks = chunk_markdown(md, "Root")

    holding = [c for c in chunks if "```python" in c.text]
    assert len(holding) == 1
    assert holding[0].text.count("```") == 2
    assert approx_tokens(code) <= MAX_TOKENS


def test_empty_page_has_no_chunks() -> None:
    assert chunk_markdown("", "Root") == []

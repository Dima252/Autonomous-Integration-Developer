"""Heading-aware Markdown chunking (docs/architecture.md §3.2).

Pages are split along their headings into chunks of roughly 500-800 tokens. Each chunk
carries a breadcrumb (`Stripe › Receive Stripe events › Create a handler`) so it still
makes sense out of context, both to the embedding model and to an agent reading a hit.
"""

import re
from dataclasses import dataclass

TARGET_TOKENS = 650
MAX_TOKENS = 800
MIN_TOKENS = 150

# Docs repeat each code sample per SDK language under a heading named after it. Generated
# integrations are Python, so the other languages are noise that costs tokens.
DROP_SECTIONS = frozenset(
    {"ruby", "node", "node.js", "php", "java", "go", ".net", "c#", "javascript", "typescript"}
)

BREADCRUMB_SEP = " › "

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ANCHOR = re.compile(r"\s*\{#[^}]*\}\s*$")
_FRONT_MATTER = re.compile(r"\A\s*---\n.*?\n---\s*\n", re.DOTALL)
_INDEX_PREAMBLE = re.compile(r"\A\s*(>[^\n]*\n)*>[^\n]*Documentation Index[^\n]*\n(>[^\n]*\n)*")
_SOURCE_LINE = re.compile(r"^Source: https?://\S+\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    breadcrumb: str
    text: str

    @property
    def tokens(self) -> int:
        return approx_tokens(self.text)


@dataclass
class _Section:
    path: tuple[str, ...]  # heading texts from the page title down to this section
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def approx_tokens(text: str) -> int:
    """~4 characters per token for English prose and code: good enough for sizing."""
    return len(text) // 4


def clean_markdown(md: str) -> str:
    """Strip site furniture that isn't documentation: front matter, "documentation
    index" preambles, source lines and heading anchors."""
    md = md.replace("\r\n", "\n")
    previous = None
    while previous != md:  # preambles and front matter can be stacked
        previous = md
        md = _FRONT_MATTER.sub("", md, count=1)
        md = _INDEX_PREAMBLE.sub("", md, count=1)
    md = _SOURCE_LINE.sub("", md)
    lines = [_ANCHOR.sub("", line) if _HEADING.match(line) else line for line in md.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def chunk_markdown(
    md: str, root: str, drop_sections: frozenset[str] = DROP_SECTIONS
) -> list[Chunk]:
    """Split a cleaned Markdown page into chunks. `root` heads every breadcrumb (usually
    the provider name); the page's H1 follows it."""
    sections = [
        s
        for s in _sections(md)
        if s.text and not any(h.strip().lower() in drop_sections for h in s.path)
    ]
    chunks: list[Chunk] = []
    group: list[_Section] = []
    for section in sections:
        for piece in _split_large(section):
            if group and _fits(group, piece):
                group.append(piece)
            else:
                if group:
                    chunks.append(_to_chunk(group, root))
                group = [piece]
    if group:
        chunks.append(_to_chunk(group, root))
    return chunks


def _sections(md: str) -> list[_Section]:
    sections = [_Section(path=(), lines=[])]
    stack: list[tuple[int, str]] = []
    in_fence = False
    for line in md.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            sections.append(_Section(path=tuple(h for _, h in stack), lines=[line]))
        else:
            sections[-1].lines.append(line)
    return sections


def _split_large(section: _Section) -> list[_Section]:
    """Split an oversized section at paragraph boundaries, never inside a code fence."""
    if approx_tokens(section.text) <= MAX_TOKENS:
        return [section]
    pieces: list[_Section] = []
    current: list[str] = []
    for block in _blocks(section.lines):
        for part in _hard_split(block):
            candidate = "\n".join([*current, part])
            if current and approx_tokens(candidate) > TARGET_TOKENS:
                pieces.append(_Section(section.path, current))
                current = []
            current.append(part)
    if current:
        pieces.append(_Section(section.path, current))
    return pieces


def _blocks(lines: list[str]) -> list[str]:
    """Paragraph-level blocks; a fenced code block is always one block."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _hard_split(block: str) -> list[str]:
    """Last resort for a single block bigger than a chunk (a huge table or code sample):
    split by lines."""
    if approx_tokens(block) <= MAX_TOKENS:
        return [block]
    parts: list[str] = []
    current: list[str] = []
    for line in block.split("\n"):
        if current and approx_tokens("\n".join([*current, line])) > TARGET_TOKENS:
            parts.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        parts.append("\n".join(current))
    return parts


def _fits(group: list[_Section], nxt: _Section) -> bool:
    size = approx_tokens("\n\n".join(s.text for s in group))
    if size + approx_tokens(nxt.text) > MAX_TOKENS:
        return False
    # Stay within one H2 topic unless the chunk so far is too small to stand alone.
    same_topic = group[0].path[:2] == nxt.path[:2]
    return size < MIN_TOKENS or (same_topic and size < TARGET_TOKENS)


def _to_chunk(group: list[_Section], root: str) -> Chunk:
    # The breadcrumb names the deepest heading every section in the chunk shares.
    path = group[0].path
    for s in group[1:]:
        common = 0
        while common < min(len(path), len(s.path)) and path[common] == s.path[common]:
            common += 1
        path = path[:common]
    crumb = BREADCRUMB_SEP.join([root, *path])
    return Chunk(breadcrumb=crumb, text="\n\n".join(s.text for s in group))

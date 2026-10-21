"""Tier 0 rules: messages made only of small-talk phrases ("hi", "thanks!", "ok cool,
bye") are answered without any model call.

A rule matches the whole message, never a prefix, so "hi, build me a Stripe webhook"
falls through to the classifier. Acknowledgements ("ok", "yes", "sounds good") are
skipped when the assistant has just asked a question, because then they are answers.
"""

import re

from aidev.router.intents import RouteContext

_PHRASES: dict[str, list[str]] = {
    "greeting": [
        r"h+i+",
        r"hello+",
        r"hey+",
        r"hiya",
        r"howdy",
        r"yo",
        r"sup",
        r"what'?s up",
        r"good (?:morning|afternoon|evening|day)",
        r"(?:morning|evening)",
        r"greetings",
    ],
    "thanks": [
        r"thanks?",
        r"thank (?:you|u)",
        r"thx",
        r"ty",
        r"tysm",
        r"cheers",
        r"(?:much|really) appreciated",
        r"(?:i )?appreciate (?:it|that)",
        r"many thanks",
    ],
    "farewell": [
        r"(?:bye)+",
        r"bye bye",
        r"goodbye",
        r"good night",
        r"gn",
        r"see (?:you|ya)(?: later| soon| tomorrow)?",
        r"later",
        r"cya",
        r"take care",
        r"have a (?:good|nice|great) (?:one|day|night|evening|weekend)",
    ],
    "smalltalk": [
        r"how are (?:you|u)(?: doing)?",
        r"how'?s it going",
        r"how are things",
        r"nice to meet (?:you|u)",
        r"(?:lol|lmao|haha+|hehe+)",
        r"you'?re (?:welcome|awesome|the best)",
        r"no worries",
        r"np",
        r"good (?:job|work)",
        r"well done",
    ],
    "ack": [
        r"ok(?:ay)?",
        r"k+",
        r"cool",
        r"great",
        r"nice",
        r"awesome",
        r"perfect",
        r"sweet",
        r"neat",
        r"wow",
        r"got it",
        r"sounds good",
        r"makes sense",
        r"yes",
        r"yep",
        r"yeah",
        r"sure",
    ],
}

# Words that can trail any phrase without changing what it means.
_FILLER = (
    r"(?:there|again|so much|a lot|very much|all|everyone|guys|folks|friend|buddy|mate|"
    r"man|claude|bot|today|then|for now|for (?:the|your) help|for that)"
)


def _sequence(classes: list[str]) -> re.Pattern[str]:
    phrase = "|".join(p for c in classes for p in _PHRASES[c])
    unit = rf"(?:{phrase})(?: {_FILLER})*"
    return re.compile(rf"(?:{unit})(?: (?:and |but )?(?:{unit})){{0,3}}")


_ANY = _sequence(list(_PHRASES))
_NO_ACK = _sequence([c for c in _PHRASES if c != "ack"])
# The class of a message's last phrase, which is what a reply should answer: "thanks,
# bye" is a farewell. Checked in this order where phrases overlap.
_ENDINGS = {
    c: re.compile(rf"(?:^| )(?:{'|'.join(_PHRASES[c])})(?: {_FILLER})*$")
    for c in ("farewell", "thanks", "greeting", "smalltalk", "ack")
}
RULE_CLASSES = (*_ENDINGS, "no_words")
# Messages over this length are never small talk by rule, whatever they contain.
MAX_CHARS = 60


def normalize(text: str) -> str:
    text = text.lower().replace("\u2019", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_smalltalk(text: str, context: RouteContext) -> str | None:
    """The class of small talk (one of RULE_CLASSES), or None."""
    if len(text) > MAX_CHARS:
        return None
    norm = normalize(text)
    if not norm:
        # Only emoji or punctuation ("🙂", "!!"). After a question, "👍" is an answer.
        return "no_words" if text.strip() and not context.awaiting_answer else None
    pattern = _NO_ACK if context.awaiting_answer else _ANY
    if not pattern.fullmatch(norm):
        return None
    return next(c for c, ending in _ENDINGS.items() if ending.search(norm))

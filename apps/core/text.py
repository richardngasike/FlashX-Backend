import re

# Hashtags: letters/digits/underscore in any script, must contain a letter.
HASHTAG_RE = re.compile(r"(?<![\w&])#([^\W_][\w]{0,99})", re.UNICODE)
MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9._]{3,30})")


def extract_hashtags(text: str) -> list[str]:
    if not text:
        return []
    seen, out = set(), []
    for tag in HASHTAG_RE.findall(text):
        tag = tag.lower()
        if tag.isdigit() or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out[:30]


def extract_mentions(text: str) -> list[str]:
    if not text:
        return []
    seen, out = set(), []
    for name in MENTION_RE.findall(text):
        name = name.rstrip(".").lower()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out[:20]

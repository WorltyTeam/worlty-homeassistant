"""Validate optional firmware display names without changing stored aliases."""

from __future__ import annotations

from typing import Any

# Match the public naming contract's ECMAScript String.trim() whitespace set.
NAME_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def contentful_name(value: Any) -> str | None:
    """Return a nonblank name verbatim, including intentional outer spaces."""
    return value if isinstance(value, str) and value.strip(NAME_WHITESPACE) else None


def display_names(value: Any) -> dict[str, str]:
    """Keep valid locales from an optional, potentially malformed extension."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for locale in ("ko", "en"):
        name = contentful_name(value.get(locale))
        if name is None or "\x00" in name or len(name) > 384:
            continue
        try:
            encoded = name.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if len(encoded) <= 1536:
            result[locale] = name
    return result


def localized_display_name(value: Any, locale: str) -> str | None:
    """Use the requested language, then English, without recomposing the name."""
    names = display_names(value)
    language = locale.replace("_", "-").split("-", 1)[0].lower()
    return names.get(language) or names.get("en")


def default_name_with_suffix(name: str, suffix: Any) -> str:
    """Add one separator before the literal legacy suffix without trimming it."""
    if isinstance(suffix, str) and suffix:
        return f"{name} {suffix}"
    return name

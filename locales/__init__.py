LANGS = ("ru", "en", "uk")
DEFAULT_LANG = "ru"


def normalize(lang, default=DEFAULT_LANG):
    """Keep a language the strings exist for, otherwise the caller's fallback
    -- the second bot falls back to Ukrainian instead of Russian."""
    return lang if lang in LANGS else default


def tr(strings, lang):
    """Pick strings[lang], falling back to Russian for a language that has
    not been given a translation for this particular key yet -- so a
    partially-translated string never renders blank."""
    return strings.get(normalize(lang), strings[DEFAULT_LANG])

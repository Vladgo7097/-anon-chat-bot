LANGS = ("ru", "en", "uk")
DEFAULT_LANG = "ru"


def normalize(lang):
    return lang if lang in LANGS else DEFAULT_LANG


def tr(strings, lang):
    """Pick strings[lang], falling back to Russian for a language that has
    not been given a translation for this particular key yet -- so a
    partially-translated string never renders blank."""
    return strings.get(normalize(lang), strings[DEFAULT_LANG])

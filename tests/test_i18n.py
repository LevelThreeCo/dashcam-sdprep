from __future__ import annotations

import re
import string
from pathlib import Path

from dashcam_sdprep import i18n


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_language_has_every_key_with_the_same_placeholders():
    english = i18n.MESSAGES["en-US"]
    for lang, catalog in i18n.MESSAGES.items():
        assert set(catalog) == set(english), f"{lang} keys differ"
        for key, text in english.items():
            assert fields(catalog[key]) == fields(text), f"{lang}:{key} placeholders differ"


def test_every_key_used_by_the_menu_exists():
    source = (Path(i18n.__file__).parent / "tui.py").read_text(encoding="utf-8")
    used = set(re.findall(r'self\.t\("([a-z_]+)"', source)) | set(re.findall(r'\bt\("([a-z_]+)"', source))
    dynamic = {k for k in used if k.endswith("_")}
    assert (used - dynamic) <= set(i18n.MESSAGES["en-US"])
    for prefix in dynamic:
        assert any(k.startswith(prefix) for k in i18n.MESSAGES["en-US"]), prefix


def test_no_em_dashes_in_any_text():
    for catalog in i18n.MESSAGES.values():
        assert not any("\u2014" in text for text in catalog.values())

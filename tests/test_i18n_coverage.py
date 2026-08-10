"""Translation coverage (sprint S0.4).

Two failure modes this guards, both of which pass every other test silently:

1. A new `_()` string ships without an Arabic translation. gettext falls back
   to the English msgid, so the page renders fine and looks right in tests
   while being wrong for every real user.

2. `pybabel extract` never sees a file at all. This actually happened: every
   shared macro lived in `app/templates/_partials/`, and Babel skips
   directories whose name starts with an underscore. The strings were absent
   from the catalogue rather than untranslated, so even a "no empty msgstr"
   check would have passed.

The second is why this walks the source tree itself instead of trusting the
catalogue's own contents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from babel.messages.extract import extract_from_dir

ROOT = Path(__file__).resolve().parent.parent
AR_PO = ROOT / "app" / "translations" / "ar" / "LC_MESSAGES" / "messages.po"

METHOD_MAP = [
    ("**.py", "python"),
    ("**.html", "jinja2"),
]

# Only skip genuinely hidden directories — NOT underscore-prefixed ones, which
# is precisely the default Babel behaviour that hid the partials.
def _directory_filter(path: str) -> bool:
    name = Path(path).name
    return not name.startswith(".") and name not in {"__pycache__", "node_modules"}


def _extracted_msgids() -> set[str]:
    found = set()
    for _filename, _lineno, message, _comments, _context in extract_from_dir(
        ROOT / "app",
        method_map=METHOD_MAP,
        keywords={"_": None, "_l": None, "gettext": None, "lazy_gettext": None},
        directory_filter=_directory_filter,
    ):
        if isinstance(message, str) and message.strip():
            found.add(message)
        elif isinstance(message, tuple):
            found.update(m for m in message if isinstance(m, str) and m.strip())
    return found


#: A PO string chunk, allowing backslash escapes inside the quotes.
_CHUNK = r'"(?:[^"\\]|\\.)*"'


def _unescape(chunks: str) -> str:
    """Join PO continuation chunks and undo the escaping.

    The unescaping matters: a msgid containing a newline is stored as a literal
    `\\n` in the file, and comparing that against the real newline extraction
    produces would report a translated string as missing forever.
    """
    raw = "".join(part[1:-1] for part in re.findall(_CHUNK, chunks))
    return raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def _catalogue() -> dict[str, str]:
    """msgid -> msgstr, handling PO line continuations and escapes."""
    text = AR_PO.read_text(encoding="utf-8")
    entries = {}
    pattern = re.compile(
        rf"^msgid ((?:{_CHUNK}\n?)+)msgstr ((?:{_CHUNK}\n?)+)", re.M
    )
    for match in pattern.finditer(text):
        msgid = _unescape(match.group(1))
        msgstr = _unescape(match.group(2))
        if msgid:
            entries[msgid] = msgstr
    return entries


@pytest.fixture(scope="module")
def coverage():
    return _extracted_msgids(), _catalogue()


def test_the_catalogue_is_not_empty(coverage):
    extracted, catalogue = coverage
    assert len(extracted) > 50, "extraction found almost nothing — check babel.cfg"
    assert len(catalogue) > 50


def test_every_user_facing_string_is_in_the_arabic_catalogue(coverage):
    """Catches a file that extraction never sees."""
    extracted, catalogue = coverage
    missing = sorted(m for m in extracted if m not in catalogue)
    assert not missing, (
        f"{len(missing)} string(s) are not in the Arabic catalogue at all. "
        f"Re-run `pybabel extract` and check babel.cfg covers their directory:\n  "
        + "\n  ".join(missing[:20])
    )


def test_every_catalogue_entry_is_translated(coverage):
    """Catches a string that was extracted but never given Arabic text."""
    extracted, catalogue = coverage
    untranslated = sorted(
        msgid for msgid, msgstr in catalogue.items() if msgid in extracted and not msgstr
    )
    assert not untranslated, (
        f"{len(untranslated)} string(s) have no Arabic translation:\n  "
        + "\n  ".join(untranslated[:20])
    )


def test_nothing_translates_a_runtime_enum_value():
    """`_(status.value)` produces no msgid and silently renders in English.

    Extraction is static, so passing a runtime value gives gettext nothing to
    key on: the string never enters the catalogue, and the coverage tests above
    cannot see it either — there is no literal to compare against. Use
    `label_for(status)` (app/labels.py), where the literals are written out.
    """
    offenders = []
    pattern = re.compile(r"_\(\s*[A-Za-z_][\w.]*\.value\s*\)")
    for folder in ("templates", "blueprints", "services"):
        for path in (ROOT / "app" / folder).rglob("*"):
            if path.suffix not in {".html", ".py"}:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}")

    assert not offenders, (
        "These translate a runtime enum value, which never reaches the "
        "catalogue. Use label_for() instead:\n  " + "\n  ".join(offenders)
    )


def test_every_enum_label_has_a_translation(coverage):
    """The labels in app/labels.py are the only place enum text is translated."""
    _extracted, catalogue = coverage
    from app.labels import _ALL

    missing = sorted(
        str(label)
        for label in _ALL.values()
        if str(label) in catalogue and not catalogue[str(label)]
    )
    assert not missing, f"Enum labels without Arabic text: {missing}"


def test_no_template_directory_is_hidden_from_babel():
    """Babel skips `_`-prefixed directories by default.

    A shared-macro folder named `_partials` therefore extracts to nothing,
    silently. Keep template directories free of that prefix.
    """
    templates = ROOT / "app" / "templates"
    offenders = [
        str(path.relative_to(templates))
        for path in templates.rglob("*")
        if path.is_dir() and path.name.startswith("_")
    ]
    assert not offenders, (
        "Babel ignores underscore-prefixed directories, so these would never "
        f"be extracted: {offenders}"
    )

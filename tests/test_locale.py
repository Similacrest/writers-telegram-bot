"""Every user-facing text lives in locale/uk.toml: no Ukrainian strings in the code, no missing keys.

A line of code may keep Cyrillic when it says why with a ``# i18n: …`` comment (input syntax, config
values such as sheet names)."""

import io
import re
import string
import tokenize
from pathlib import Path

import pytest

from writers_bot.core.i18n import raw, texts
from writers_bot.core.settings import BY_KEY as SETTING_KEYS

SRC = Path(__file__).parent.parent / "src" / "writers_bot"
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
KEY_RE = re.compile(r"""["']((?:[a-z_]+)\.[a-z_0-9]+)["']""")
SECTIONS = {k.split(".")[0] for k in texts()}


def source_files():
    return sorted(p for p in SRC.rglob("*.py"))


def _string_tokens(path: Path):
    """String literals that aren't docstrings, with their line numbers."""
    tokens = list(tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline))
    significant = [t for t in tokens if t.type not in (tokenize.NL, tokenize.COMMENT)]
    for i, tok in enumerate(significant):
        if tok.type != tokenize.STRING and tok.type != tokenize.FSTRING_START:
            continue
        prev = significant[i - 1] if i else None
        nxt = significant[i + 1] if i + 1 < len(significant) else None
        docstring = (prev is None or prev.type in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT)) and (
            nxt is not None and nxt.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
        )
        if not docstring:
            yield tok.start[0], tok.line


@pytest.mark.parametrize("path", source_files(), ids=lambda p: str(p.relative_to(SRC)))
def test_no_ukrainian_strings_in_code(path):
    offending = []
    for lineno, line in _string_tokens(path):
        if "# i18n:" in line:
            continue
        code = line.split("#", 1)[0] if "#" in line and not CYRILLIC.search(line.split("#", 1)[0]) else line
        if CYRILLIC.search(code):
            offending.append(f"{lineno}: {line.strip()}")
    # f-string middles show up as separate tokens; check whole lines of code as well
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if CYRILLIC.search(code) and ('f"' in code or "f'" in code) and "# i18n:" not in line:
            offending.append(f"{lineno}: {line.strip()}")
    assert not offending, "move these texts to locale/uk.toml:\n" + "\n".join(sorted(set(offending)))


def test_every_used_key_exists():
    missing = set()
    for path in source_files():
        for key in KEY_RE.findall(path.read_text(encoding="utf-8")):
            if (
                key.split(".")[0] in SECTIONS
                and key.rsplit(".", 1)[1] not in ("csv", "zip")
                and key not in SETTING_KEYS  # chat settings are "<module>.<name>" too
                and key not in texts()
            ):
                missing.add(f"{key} ({path.relative_to(SRC)})")
    assert not missing, "\n".join(sorted(missing))


@pytest.mark.parametrize(
    "keys",
    [
        [f"goals.per_{p}" for p in ("day", "week", "month")],
        [f"share.period_{p}" for p in ("today", "week", "month", "year")],
        [f"admin.target_{p}" for p in ("groups", "private", "all")],
        [f"works.period_{p}" for p in ("day", "week", "month")],
    ],
)
def test_dynamic_keys_exist(keys):
    for key in keys:
        raw(key)


def test_templates_parse():
    for value in texts().values():
        for template in value if isinstance(value, list) else [value]:
            list(string.Formatter().parse(str(template)))  # raises on unbalanced braces


def test_plural_forms():
    for key, value in texts().items():
        if key.startswith("plural."):
            assert isinstance(value, list) and len(value) == 3, key


def test_every_key_has_a_comment():
    """Each key (or the block it belongs to) has a comment saying where it appears."""
    lines = (SRC / "locale" / "uk.toml").read_text(encoding="utf-8").splitlines()
    uncommented = []
    for i, line in enumerate(lines):
        if not re.match(r"^[a-z_0-9]+ = ", line):
            continue
        j = i - 1
        while j >= 0 and re.match(r"^[a-z_0-9]+ = ", lines[j]):
            j -= 1  # a run of keys shares the comment above it
        if j < 0 or not (lines[j].startswith("#") or lines[j].startswith("[")):
            uncommented.append(line.split(" = ")[0])
    assert not uncommented, uncommented


def test_web_app_texts():
    js = (SRC / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert not CYRILLIC.search(js), "the web app takes its texts from the locale file"
    used = {k for k in re.findall(r'T\("([a-z_0-9]+)"', js) if not k.endswith("_")}
    for prefix, names in {
        "tab_": ("sprint", "works", "progress", "settings", "chat", "prompts", "achievements"),
        "status_": ("planned", "running", "reporting", "closed", "cancelled"),
        "per_": ("day", "week", "month"),
        "outcome_": ("done", "failed"),
    }.items():
        used |= {prefix + n for n in names}
    missing = [k for k in used if f"web.{k}" not in texts()]
    assert not missing, missing


def test_every_setting_has_a_label():
    for key in SETTING_KEYS:
        name = "chatsettings." + key.replace(".", "_")
        raw(name)
        raw(name + "_help")


def test_every_help_group_has_a_title_and_every_die_a_ukrainian_name():
    """/help names a group for each module (and your data, the chat, the bot's admins); each die has its !к name."""
    from writers_bot.core.i18n import t
    from writers_bot.modules import MODULES
    from writers_bot.telegram_bot.app import HELP_GROUPS

    for name in (*MODULES, *HELP_GROUPS):
        assert t(f"help.group_{name}"), name
    for sides in (2, 4, 6, 8, 10, 12, 20, 100):
        assert t(f"aliases.d{sides}") == f"к{sides}"

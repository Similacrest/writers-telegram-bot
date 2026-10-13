"""Read chat ids out of the v2 bot's ``db.pickle`` (python-telegram-bot PicklePersistence).

PTB writes an (often empty) ``chat_data``/``user_data`` entry for every chat and user it processed an
update from, so the keys are a good list of chats the old bot was used in.

The file is read with a restricted unpickler: only plain containers and scalars are built, any other
class becomes an inert placeholder, and PTB's persistent ids (bot references) resolve to ``None``.
Nothing from the file is imported or executed.
"""

import builtins
import pickle
from pathlib import Path

_SAFE_BUILTINS = {"dict", "list", "set", "frozenset", "tuple", "int", "float", "str", "bytes", "bool", "complex"}


class _Placeholder:
    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        pass

    def __call__(self, *args, **kwargs):
        return _Placeholder()


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "builtins" and name in _SAFE_BUILTINS:
            return getattr(builtins, name)
        if module == "collections" and name in {"defaultdict", "OrderedDict"}:
            return dict if name == "OrderedDict" else _defaultdict
        return _Placeholder

    def persistent_load(self, pid):
        return None


def _defaultdict(*_args):
    return {}


def read_legacy_ids(path: str | Path) -> tuple[set[int], set[int]]:
    """Return (chat ids, user ids) found in a PTB ``PicklePersistence`` single file."""
    with open(path, "rb") as f:
        data = _SafeUnpickler(f).load()
    if not isinstance(data, dict):
        raise ValueError("not a PicklePersistence file")

    def int_keys(value) -> set[int]:
        return {k for k in value if isinstance(k, int)} if isinstance(value, dict) else set()

    return int_keys(data.get("chat_data")), int_keys(data.get("user_data"))


def guess_chat_type(chat_id: int) -> str:
    # Telegram ids: users are positive, basic groups negative, supergroups/channels start with -100.
    if chat_id > 0:
        return "private"
    return "supergroup" if str(chat_id).startswith("-100") else "group"

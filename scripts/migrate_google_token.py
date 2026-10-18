#!/usr/bin/env python
"""One-time conversion of the old ``token.pickle.b64`` Google token into ``token.json``.

The bot no longer unpickles anything at runtime. Run this once, by hand, on a token file you trust:

    uv run python scripts/migrate_google_token.py token.pickle.b64 data/token.json
"""

import base64
import os
import pickle
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <token.pickle.b64> <token.json>")
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    creds = pickle.loads(base64.b64decode(src.read_bytes()))  # noqa: S301 - explicit one-off on a trusted file
    to_json = getattr(creds, "to_json", None)
    if to_json is None:
        raise SystemExit(f"unexpected object in {src}: {type(creds).__name__}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(to_json())
    os.chmod(dst, 0o600)
    print(f"wrote {dst}; you can now delete {src}")


if __name__ == "__main__":
    main()

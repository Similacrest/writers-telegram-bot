#!/usr/bin/env python
"""Create ``token.json`` for a Google user account (run on a machine with a browser).

    GOOGLE_API_CLIENT_ID=... GOOGLE_API_CLIENT_SECRET=... \
        uv run --extra google-auth-setup python scripts/google_auth.py data/token.json

Alternatively skip this and use a service account (GOOGLE_SERVICE_ACCOUNT_FILE) shared on the sheet
and the Drive folder.
"""

import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> None:
    dst = Path(sys.argv[1] if len(sys.argv) > 1 else "token.json")
    config = {
        "installed": {
            "client_id": os.environ["GOOGLE_API_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_API_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    creds = InstalledAppFlow.from_client_config(config, SCOPES).run_local_server(host="localhost", port=0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(creds.to_json())
    os.chmod(dst, 0o600)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()

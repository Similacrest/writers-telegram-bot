"""Writing prompts and bot config loaded from Google Sheets and Google Drive.

Credentials are read from JSON only: a service account key (``GOOGLE_SERVICE_ACCOUNT_FILE``) or an
authorized-user token (``GOOGLE_TOKEN_FILE``, created by ``scripts/google_auth.py`` or converted from the
old ``token.pickle.b64`` with ``scripts/migrate_google_token.py``). The old pickle is never loaded at
runtime: unpickling a file is arbitrary code execution if the file is ever replaced.

The Google client is synchronous, so loading runs in a worker thread and swaps the data in only after
everything loaded, so a failed ``/reload`` keeps the old prompts.
"""

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

USER_SCOPES = ["https://www.googleapis.com/auth/drive"]  # what existing user tokens were granted
SERVICE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


class PromptsUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GoogleSettings:
    spreadsheet_id: str
    config_sheet: str | None
    drive_folder_id: str | None
    prompt_sheets: dict[str, str]  # language -> sheet name
    prompt_range: str
    token_file: Path
    service_account_file: Path | None


@dataclass(slots=True)
class PromptData:
    config: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    texts: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # lang -> category -> prompts
    images: dict[str, list[dict]] = field(default_factory=dict)  # folder -> files


def parse_config_rows(rows: list[list[str]]) -> dict[str, dict[str, list[str]]]:
    """Config sheet rows are ``key | subkey | (comment) | value...``. Values are kept as lists of cells."""
    config: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        if not row or not row[0].strip():
            continue
        if len(row) < 4:
            logger.warning("config row %r has no value, skipped", row[0])
            continue
        key, subkey = row[0].strip(), row[1].strip()
        config.setdefault(key, {})[subkey] = [cell for cell in row[3:]]
    return config


def parse_prompt_columns(columns: list[list[str]]) -> dict[str, list[str]]:
    """Each sheet column is a category: header in the first row, prompts below (issue #4: any number)."""
    categories: dict[str, list[str]] = {}
    for col in columns:
        if not col or not col[0].strip():
            continue
        values = [v.strip() for v in col[1:] if v and v.strip()]
        if values:
            categories[col[0].strip()] = values
    return categories


class PromptsStore:
    def __init__(self, settings: GoogleSettings | None):
        self.settings = settings
        self.data = PromptData()

    @property
    def enabled(self) -> bool:
        return self.settings is not None

    async def load(self) -> None:
        if self.settings is None:
            raise PromptsUnavailableError("Google Sheets is not configured")
        self.data = await asyncio.to_thread(self._load_sync, self.settings)
        logger.info(
            "prompts loaded: %s",
            {lang: len(cats) for lang, cats in self.data.texts.items()} | {"image folders": len(self.data.images)},
        )

    # --- queries ---------------------------------------------------------------------------------

    def config_text(self, key: str, subkey: str = "") -> str | None:
        values = self.data.config.get(key, {}).get(subkey)
        return "\n".join(values) if values else None

    def text_categories(self, lang: str) -> list[str]:
        return list(self.data.texts.get(lang, {}))

    def find_category(self, lang: str, query: str) -> str | None:
        query = query.strip().lower()
        cats = self.text_categories(lang)
        for cat in cats:
            if cat.lower() == query:
                return cat
        matches = [c for c in cats if c.lower().startswith(query)]
        return matches[0] if len(matches) == 1 else None

    def random_text(self, lang: str, category: str | None = None) -> dict[str, str]:
        cats = self.data.texts.get(lang)
        if not cats:
            raise PromptsUnavailableError("no prompts loaded")
        if category is not None:
            return {category: random.choice(cats[category])}
        return {k: random.choice(v) for k, v in cats.items()}

    def image_categories(self) -> list[str]:
        return [c for c in self.data.images if c != "all"]

    def random_image(self, category: str | None) -> tuple[str, int, dict]:
        cat = (category or "all").lower()
        if cat not in self.data.images:
            cat = "all"
        images = self.data.images.get(cat) or []
        if not images:
            raise PromptsUnavailableError("no images loaded")
        num = random.randrange(len(images))
        return cat, num, dict(images[num])

    def stats(self) -> dict[str, int]:
        stats = {}
        for lang, cats in self.data.texts.items():
            for cat, values in cats.items():
                stats[f"{lang}-{cat}"] = len(values)
        for folder, files in self.data.images.items():
            stats[f"image-{folder}"] = len(files)
        return stats

    # --- loading (runs in a thread) --------------------------------------------------------------

    @staticmethod
    def _credentials(settings: GoogleSettings):
        from google.auth.transport.requests import Request

        if settings.service_account_file:
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(
                str(settings.service_account_file), scopes=SERVICE_SCOPES
            )

        from google.oauth2.credentials import Credentials

        if not settings.token_file.exists():
            legacy = settings.token_file.with_name("token.pickle.b64")
            hint = " Run scripts/migrate_google_token.py to convert token.pickle.b64." if legacy.exists() else ""
            raise PromptsUnavailableError(f"Google token {settings.token_file} not found.{hint}")
        info = json.loads(settings.token_file.read_text())
        creds = Credentials.from_authorized_user_info(info, scopes=info.get("scopes") or USER_SCOPES)
        if not creds.valid:
            if not creds.refresh_token:
                raise PromptsUnavailableError("Google token expired and has no refresh token")
            creds.refresh(Request())
            tmp = settings.token_file.with_suffix(".tmp")
            tmp.write_text(creds.to_json())
            os.chmod(tmp, 0o600)
            tmp.replace(settings.token_file)
        return creds

    @classmethod
    def _load_sync(cls, settings: GoogleSettings) -> PromptData:
        from googleapiclient.discovery import build

        logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
        creds = cls._credentials(settings)
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets().values()
        data = PromptData()

        if settings.config_sheet:
            rows = (
                sheets.get(spreadsheetId=settings.spreadsheet_id, range=f"'{settings.config_sheet}'!A2:Z")
                .execute()
                .get("values", [])
            )
            data.config = parse_config_rows(rows)

        for lang, sheet_name in settings.prompt_sheets.items():
            columns = (
                sheets.get(
                    spreadsheetId=settings.spreadsheet_id,
                    range=f"'{sheet_name}'!{settings.prompt_range}",
                    majorDimension="COLUMNS",
                )
                .execute()
                .get("values", [])
            )
            data.texts[lang] = parse_prompt_columns(columns)

        if settings.drive_folder_id:
            data.images = cls._load_images(
                build("drive", "v3", credentials=creds, cache_discovery=False).files(), settings
            )
        return data

    @staticmethod
    def _list_all(files, **kwargs) -> list[dict]:
        result, token = [], None
        while True:
            page = files.list(pageSize=1000, pageToken=token, **kwargs).execute()
            result += page.get("files", [])
            token = page.get("nextPageToken")
            if not token:
                return result

    @classmethod
    def _load_images(cls, files, settings: GoogleSettings) -> dict[str, list[dict]]:
        base = settings.drive_folder_id.replace("'", "")
        images: dict[str, list[dict]] = {"all": []}
        folders = cls._list_all(
            files,
            q=f"'{base}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="nextPageToken, files(id, name)",
        )
        for folder in folders:
            name = folder["name"].strip().lower()
            if name == "all" or folder["id"] == base:
                continue
            folder_id = folder["id"].replace("'", "")
            found = cls._list_all(
                files,
                q=f"'{folder_id}' in parents and mimeType contains 'image/' and trashed = false",
                fields="nextPageToken, files(id, name, webContentLink, webViewLink)",
            )
            images[name] = found
            images["all"] += found
        return images

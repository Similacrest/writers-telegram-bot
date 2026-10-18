"""Link counting service: the number of words behind a link, for works and ``/count``."""

from .netguard import BlockedURLError, FetchError, SafeFetcher
from .sources import LinkedText, UnsupportedLinkError, fetch_linked_text
from .wordcount import count_words

LINK_ERRORS = (UnsupportedLinkError, BlockedURLError, FetchError)


class LinkError(Exception):
    """The link could not be counted; the message is shown to the user."""


class LinkCounter:
    def __init__(self, fetcher: SafeFetcher | None = None, *, all_sources: bool = True):
        self.fetcher = fetcher
        self.all_sources = all_sources

    async def start(self) -> None:
        if self.fetcher is None:
            self.fetcher = SafeFetcher()

    async def close(self) -> None:
        if self.fetcher is not None:
            await self.fetcher.aclose()

    async def fetch(self, url: str) -> LinkedText:
        try:
            return await fetch_linked_text(self.fetcher, url, all_sources=self.all_sources)
        except LINK_ERRORS as e:
            raise LinkError(str(e)) from e

    async def count(self, url: str) -> int:
        return count_words((await self.fetch(url)).text)

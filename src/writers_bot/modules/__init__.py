"""The bot's modules. Each one brings its handlers, commands (menu, /help, Ukrainian aliases), options,
tables and web app pages, and adds sections to the shared screens (/profile, /help, the web app).

Modules never call each other; they share only the core and the database. A bot (see
:mod:`writers_bot.botconfig`) is a set of modules plus its platforms, and the services a module needs
(storage, link counting, the web app) are started only when some module of that bot needs them.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.storage import ModuleSchema

PLATFORMS = frozenset({"telegram", "discord"})


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    name: str
    defaults: dict = field(default_factory=dict)
    needs_fn: Callable[[dict], set[str]] = lambda _options: set()
    platforms: frozenset[str] = PLATFORMS
    schema: ModuleSchema | None = None
    description: str = ""

    def needs(self, options: dict) -> set[str]:
        return set(self.needs_fn(self.defaults | options))

    def part(self, platform: str):
        """The module's adapter class for ``platform`` (imported on demand)."""
        if platform not in self.platforms:
            return None
        module = importlib.import_module(f"{__name__}.{self.name}.{platform}")
        return module.Module


def _sprints_needs(o: dict) -> set[str]:
    needs = {"storage", "people"}
    if o.get("works"):
        needs.add("links")
    if o.get("reports") and o.get("web", True):
        needs.add("web")
    return needs


def _prompts_needs(o: dict) -> set[str]:
    return {"storage"} if o.get("source") == "db" else set()


def _schema(name: str) -> ModuleSchema | None:
    try:
        module = importlib.import_module(f"{__name__}.{name}.schema")
    except ModuleNotFoundError:
        return None
    return module.SCHEMA


MODULES: dict[str, ModuleSpec] = {
    spec.name: spec
    for spec in [
        ModuleSpec(
            "prompts",
            defaults={"source": "sheet", "shared": True, "categories": False},
            needs_fn=_prompts_needs,
            description="text and image prompts",
        ),
        ModuleSpec("wordcount", needs_fn=lambda o: {"links"}, description="word counter"),
        ModuleSpec(
            "sprints",
            defaults={"reports": False, "works": False, "percent": False, "web": True},
            needs_fn=_sprints_needs,
            description="writing sprints",
        ),
        ModuleSpec(
            "greeter",
            defaults={"texts": {}, "from_sheet": False},
            platforms=frozenset({"telegram"}),
            description="welcome messages",
        ),
        ModuleSpec("dice", platforms=frozenset({"telegram"}), description="dice rolls"),
        ModuleSpec("smash", description="keyboard smash"),
        ModuleSpec(
            "replies",
            defaults={"entries": [], "legacy": False},
            platforms=frozenset({"telegram"}),
            description="answers to commands and phrases",
        ),
        ModuleSpec(
            "achievements",
            defaults={},
            needs_fn=lambda o: {"storage", "people"},
            platforms=frozenset({"telegram"}),
            description="achievements",
        ),
    ]
}


def schemas(names) -> list[ModuleSchema]:
    return [s for n in names if (s := _schema(n)) is not None]

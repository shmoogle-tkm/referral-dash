"""Mission names for type-ahead when someone types their mission in setup.

The list ships with the app (data/missions.json) and comes from the Church News list of all
current missions. Tableau can't supply it: each account only sees its own mission. Missions are
combined and renamed each year, so refresh that file before a release; any typed name still works.
"""
from __future__ import annotations

import json
import unicodedata

from .settings import app_directory

FILE_NAME = "missions.json"


def load() -> list[str]:
    try:
        data = json.loads((app_directory() / "data" / FILE_NAME).read_text(encoding="utf-8"))
        return [str(name) for name in data["missions"] if str(name).strip()]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def fold(name: str) -> str:
    """A name with accents, capitals, curly apostrophes, periods and extra spaces taken out.

    "México Mexico City East Mission" and "Mexico Mexico City East Mission" fold the same,
    so a choice from the list still finds Tableau's spelling.
    """
    text = unicodedata.normalize("NFKD", str(name).replace("’", "'"))
    text = "".join(c for c in text if not unicodedata.combining(c)).replace(".", "")
    return " ".join(text.split()).casefold()

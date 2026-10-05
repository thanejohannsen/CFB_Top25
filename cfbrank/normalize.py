"""Team-name canonicalization and platform-stable collation.

/ratings/fpi carries no team id, so joining it to /games must go through the
name. These helpers keep that join total and deterministic.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

# Spelling differences observed between CFBD endpoints and common variants.
# Deliberately does NOT fold distinct schools together: "Texas A&M-Commerce"
# must never collapse into "Texas A&M", nor "Miami (OH)" into "Miami".
ALIASES: dict[str, str] = {
    "Miami (FL)": "Miami",
    "Miami FL": "Miami",
    "Hawai'i": "Hawaii",
    "Hawai`i": "Hawaii",
    "San Jose State": "San José State",
    "Massachusetts": "UMass",
    "Louisiana-Monroe": "Louisiana Monroe",
    "Southern Mississippi": "Southern Miss",
    "Texas-San Antonio": "UTSA",
    "Texas-El Paso": "UTEP",
    "Central Florida": "UCF",
    "Southern California": "USC",
    "Pitt": "Pittsburgh",
    "Ole Miss": "Ole Miss",
    "NC State": "NC State",
    "North Carolina State": "NC State",
    "App State": "Appalachian State",
}


def canonical(name: str) -> str:
    """NFC-normalize, trim, collapse internal whitespace, then apply aliases."""
    if name is None:
        return ""
    s = unicodedata.normalize("NFC", str(name)).strip()
    s = " ".join(s.split())
    return ALIASES.get(s, s)


def _fold(name: str) -> str:
    """ASCII-fold and casefold so collation never depends on locale."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return ascii_only.casefold()


def sort_key(name: str) -> tuple[str, str]:
    """Deterministic, total collation key for team names.

    Every sort in this package terminates in this so that output bytes do not
    depend on dict insertion order, locale, or Python version.
    """
    return (_fold(name), name)


def is_probably_fcs(classification: str | None) -> bool:
    """True when a game participant is not an FBS team."""
    return bool(classification) and classification.lower() != "fbs"


def join_report(
    rating_names: Iterable[str], fbs_game_names: Iterable[str]
) -> dict[str, list[str]]:
    """Names present on one side of the fpi/games join but not the other."""
    ratings = {canonical(n) for n in rating_names}
    played = {canonical(n) for n in fbs_game_names}
    return {
        "ratings_without_games": sorted(ratings - played, key=sort_key),
        "games_without_ratings": sorted(played - ratings, key=sort_key),
    }

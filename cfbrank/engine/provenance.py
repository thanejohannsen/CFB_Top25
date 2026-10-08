"""Templated justification sentences.

These are the exact strings the site renders, so the page needs no ranking
logic of its own. Templates rather than free text keeps them testable.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from cfbrank.engine.base_score import TeamBase
from cfbrank.engine.evidence import EdgeFact

# `site` is recorded from the winner's point of view, so the loser's venue is
# the mirror of it.
SITE_PHRASE = {"home": "at home", "away": "on the road", "neutral": "at a neutral site"}
LOSER_SITE_PHRASE = {"home": "on the road", "away": "at home", "neutral": "at a neutral site"}


def site_phrase(fact: EdgeFact, team: str) -> str:
    table = SITE_PHRASE if team == fact.winner else LOSER_SITE_PHRASE
    return table.get(fact.site, fact.site)


def opponent_label(team: str, rank: int | None) -> str:
    return f"#{rank} {team}" if rank else team


def describe_result(fact: EdgeFact, opponent_rank: int | None = None) -> str:
    """"beat #6 Oregon 24-21 on the road in week 4" -- from the winner's side."""
    return (
        f"beat {opponent_label(fact.loser, opponent_rank)} {fact.score}"
        f" {site_phrase(fact, fact.winner)} in week {fact.week}"
    )


def describe_loss(fact: EdgeFact, opponent_rank: int | None = None) -> str:
    """"lost to #6 Oregon 21-24 at home in week 4" -- from the loser's side."""
    return (
        f"lost to {opponent_label(fact.winner, opponent_rank)}"
        f" {fact.loser_points}-{fact.winner_points}"
        f" {site_phrase(fact, fact.loser)} in week {fact.week}"
    )


# How a set of grounds reads in a sentence. Keyed on `Grounds.category`, so a new
# category cannot be added without deciding what it says.
GROUNDS_PHRASE = {
    "none": "nothing has happened since to undermine it",  # the game is this week
    "age": "nothing has happened since beyond the weeks going by",
    "form": "the winner has slipped since",
    "resume": "the loser has beaten better teams since",
    "both": "the winner has slipped since and the loser has beaten better teams",
}


def grounds_reason(fact: EdgeFact) -> str:
    """What has happened since the game, and how far apart that lets the two sit.

    This is the second half of the answer to "my team beat them and is ranked
    below them": the first half is how weak the game was (`override_reason`), and
    this is what the weeks since have done to it.
    """
    g = fact.grounds
    if g is None:
        return ""
    bits: list[str] = []
    if g.slide > 0:
        if not g.loser_losses:
            bits.append(f"{fact.winner} has lost {g.winner_losses} since and {fact.loser} none")
        elif g.winner_losses > g.loser_losses:
            bits.append(
                f"{fact.winner} has lost {g.winner_losses} since"
                f" against {fact.loser}'s {g.loser_losses}"
            )
        else:
            # Having lost at all is grounds in its own right, so this fires even
            # when the loser has slipped further. It is worth little: see
            # `loss_offset` in engine/grounds.py.
            bits.append(
                f"both have lost since -- {fact.winner} {g.winner_losses},"
                f" {fact.loser} {g.loser_losses}"
            )
    if g.ascent > 0:
        bits.append(
            f"{fact.loser}'s wins since average {g.ascent:.1f} rating points better"
            f" than {fact.winner}'s"
            + (f", at {g.ramp * 100:.0f}% force {g.weeks_since:.0f} weeks on" if g.ramp < 1 else "")
        )
    if not bits:
        bits.append(GROUNDS_PHRASE.get(g.category, g.category))
    places = round(g.allowance)
    worth = f"worth {places} place{'' if places == 1 else 's'} of separation"
    verdict = "no grounds at all" if g.severity == "none" else f"{g.severity} grounds"
    return f"{'; '.join(bits)} -- {verdict}, {worth}"


def override_reason(fact: EdgeFact) -> str:
    """Why ranking against this result was the cheaper option."""
    bits: list[str] = []
    if fact.adj_margin < 2.0:
        bits.append(
            f"a {fact.margin}-point win {SITE_PHRASE.get(fact.site, fact.site)}"
            f" is worth {fact.adj_margin:+.1f} once venue is accounted for"
        )
    else:
        bits.append(f"{fact.adj_margin:+.1f} adjusted margin")
    if fact.rating_gap < -1.0:
        bits.append(
            f"the winner rates {abs(fact.rating_gap):.1f} points worse on the power ratings"
        )
    if fact.common_opponents and fact.common_diff < 0:
        shared = len(fact.common_opponents)
        bits.append(
            f"the loser fared better against {shared} shared"
            f" opponent{'' if shared == 1 else 's'}"
        )
    reason = "; ".join(bits)
    since = grounds_reason(fact)
    return f"{reason}. Since then: {since}" if since else reason


def team_reasons(
    tb: TeamBase,
    final_rank: int,
    honored_wins: Sequence[tuple[EdgeFact, int]],
    overridden_losses: Sequence[tuple[EdgeFact, int]],
    overridden_wins: Sequence[tuple[EdgeFact, int]],
    cycle_id: str | None,
    cycle_size: int,
) -> list[str]:
    out: list[str] = [
        f"Resume order #{tb.raw_rank} from {tb.formula()}."
    ]

    if abs(tb.resume_adj) > 0.05:
        # Name the component that actually drove the total, not the largest
        # signed value -- for a credit the biggest mover is the most negative.
        driver = max(tb.resume_components.items(), key=lambda kv: abs(kv[1]))
        label = driver[0].replace("_", " ")
        if tb.resume_adj > 0:
            out.append(
                f"Resume adjustment cost {tb.resume_adj:.2f} rank points, driven by {label}."
            )
        else:
            out.append(
                f"Resume adjustment earned back {abs(tb.resume_adj):.2f} rank points, "
                f"driven by {label}."
            )

    for note in tb.regression_notes[:2]:
        out.append(note + ".")

    if honored_wins:
        top = honored_wins[0]
        out.append(f"Head-to-head held: {describe_result(top[0], top[1])}.")

    for fact, rank in overridden_losses[:2]:
        out.append(
            f"Ranked above {opponent_label(fact.winner, rank)} despite losing"
            f" {fact.loser_points}-{fact.winner_points} {site_phrase(fact, tb.team)}"
            f" in week {fact.week} -- {override_reason(fact)}."
        )
    for fact, rank in overridden_wins[:2]:
        out.append(
            f"Ranked below {opponent_label(fact.loser, rank)} despite winning"
            f" {fact.winner_points}-{fact.loser_points} {site_phrase(fact, tb.team)}"
            f" in week {fact.week} -- {override_reason(fact)}."
        )

    if cycle_id:
        out.append(
            f"Part of contradiction loop {cycle_id}, a {cycle_size}-team tangle of "
            "results that no ordering can fully satisfy."
        )

    drift = tb.base_rank - final_rank
    if drift > 0:
        out.append(f"Moved up {drift} from its base position of #{tb.base_rank}.")
    elif drift < 0:
        out.append(f"Moved down {abs(drift)} from its base position of #{tb.base_rank}.")
    else:
        out.append("Head-to-head results left this placement unchanged.")

    return out


def cycle_explanation(members: Sequence[str], kept: int, overridden: Sequence[EdgeFact]) -> str:
    names = ", ".join(members[:-1]) + f" and {members[-1]}" if len(members) > 1 else members[0]
    base = (
        f"{names} form a loop: following every head-to-head result among them leads "
        f"back to where it started, so no ordering can honour all {kept + len(overridden)} of them."
    )
    if not overridden:
        return base + " The ordering below honours every one of them."
    worst = min(overridden, key=lambda f: f.weight)
    lead = (
        f" The weakest link was {worst.winner}'s {worst.score} win over {worst.loser}"
        f" ({override_reason(worst)})"
    )
    if len(overridden) == 1:
        return base + lead + ", so it was set aside."
    others = len(overridden) - 1
    return (
        base
        + lead
        + f", so it was set aside, along with {others} other result"
        + ("s" if others != 1 else "")
        + " in this loop."
    )

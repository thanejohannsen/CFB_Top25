"""Templated justification sentences.

These are the exact strings the site renders, so the page needs no ranking
logic of its own. Templates rather than free text keeps them testable.
"""

from __future__ import annotations

from typing import Mapping, NamedTuple, Sequence

from cfbrank.engine.base_score import TeamBase
from cfbrank.engine.evidence import EdgeFact


class Holding(NamedTuple):
    """Why the loser of a set-aside result still finishes above its winner.

    An exception only says the ranking MAY go against a result. What actually
    puts the two teams in that order is often a chain of other results the
    ranking is honouring, and saying "the resume order decides" when the resume
    said the opposite is simply wrong. On 2026 week 8 the resume had Ole Miss
    fourteen places above Missouri; Missouri beat Florida and Florida beat Ole
    Miss, and that pair is what held the published order.

    `path` runs loser -> ... -> winner, each hop a result the ranking honours.
    `enforced` is True only when every hop is one no exception could unlock, so
    the chain is unbreakable rather than merely preferred. `resume_gap` is how
    many places the resume order had the WINNER above the loser, positive only
    when the published order contradicts it.
    """

    path: tuple[str, ...] = ()
    enforced: bool = False
    resume_gap: int = 0

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


# One clause per exception, in the owner's own terms. Keyed on the rule number, so
# a new rule cannot be added without deciding what it says.
RULE_PHRASE = {
    1: "{winner} has more losses, {wl} to {ll}",
    2: "{winner} has lost {ws} since and {loser} none",
    3: "{winner} has lost {ws} since against {loser}'s {ls}",
    4: "{loser}'s wins since average {diff:.1f} rating points better",
}

ENFORCED_PHRASE = "nothing since the game lets this result be ranked against"


def chain_phrase(path: Sequence[str]) -> str:
    """`(A, B, C)` -> "A beat B, B beat C"."""
    return ", ".join(f"{a} beat {b}" for a, b in zip(path, path[1:]))


def holding_reason(fact: EdgeFact, holding: Holding | None) -> str:
    """The sentence that names what actually produced the published gap."""
    if not holding or not holding.path:
        return ""
    bits = []
    if holding.resume_gap > 0:
        places = holding.resume_gap
        bits.append(
            f"The resume put {fact.winner} {places}"
            f" place{'' if places == 1 else 's'} higher"
        )
    kind = "an enforced chain" if holding.enforced else "results the ranking honours"
    bits.append(f"{fact.loser} is held above by {kind}: {chain_phrase(holding.path)}")
    # No trailing stop: every caller closes the sentence itself, the way the
    # clause before this one does.
    return ". " + "; ".join(bits)


def grounds_reason(fact: EdgeFact, holding: Holding | None = None) -> str:
    """Which of the four exceptions unlocked this override, and how far it goes.

    The second half of the answer to "my team beat them and is ranked below
    them": the first half is how weak the game was (`override_reason`), and this
    is what has happened since that permits going against it at all.
    """
    g = fact.grounds
    if g is None:
        return ""
    if g.enforced:
        return ENFORCED_PHRASE

    fields = {
        "winner": fact.winner, "loser": fact.loser,
        "wl": g.winner_losses, "ll": g.loser_losses,
        "ws": g.winner_losses_since, "ls": g.loser_losses_since,
        "diff": g.quality_diff,
    }
    # Rule 2 is the N=1 case of rule 3, so when both hold only the sharper
    # sentence is worth printing.
    spoken = [r for r in g.rules if not (r == 3 and 2 in g.rules)]
    bits = [RULE_PHRASE[r].format(**fields) for r in spoken]

    if g.banded:
        places = int(g.max_lead)
        bits.append(
            f"{g.weeks_since:.0f} weeks on, so they may lead by"
            f" {places} place{'' if places == 1 else 's'}"
        )
    elif holding and holding.path:
        # The resume did NOT decide this one, so do not say it did.
        bits.append("so this result was free to set aside")
    else:
        bits.append("so the resume order decides how far apart they sit")
    return "; ".join(bits)


def override_reason(fact: EdgeFact, holding: Holding | None = None) -> str:
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
    since = grounds_reason(fact, holding)
    out = f"{reason}. Since then: {since}" if since else reason
    return out + holding_reason(fact, holding)


def team_reasons(
    tb: TeamBase,
    final_rank: int,
    honored_wins: Sequence[tuple[EdgeFact, int]],
    overridden_losses: Sequence[tuple[EdgeFact, int]],
    overridden_wins: Sequence[tuple[EdgeFact, int]],
    cycle_id: str | None,
    cycle_size: int,
    holdings: Mapping[tuple[str, str], Holding] | None = None,
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

    held = holdings or {}
    for fact, rank in overridden_losses[:2]:
        out.append(
            f"Ranked above {opponent_label(fact.winner, rank)} despite losing"
            f" {fact.loser_points}-{fact.winner_points} {site_phrase(fact, tb.team)}"
            f" in week {fact.week} -- "
            f"{override_reason(fact, held.get((fact.winner, fact.loser)))}."
        )
    for fact, rank in overridden_wins[:2]:
        out.append(
            f"Ranked below {opponent_label(fact.loser, rank)} despite winning"
            f" {fact.winner_points}-{fact.loser_points} {site_phrase(fact, tb.team)}"
            f" in week {fact.week} -- "
            f"{override_reason(fact, held.get((fact.winner, fact.loser)))}."
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

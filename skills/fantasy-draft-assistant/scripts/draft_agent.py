#!/usr/bin/env python3
"""
Fantasy Draft Assistant — Real-time player recommendations during live drafts.

Combines live game context (via Shipp.ai) with season stats (via public APIs)
to rank available players by fantasy value during NBA and MLB drafts.

Usage:
    python draft_agent.py --sport nba --format points
    python draft_agent.py --sport mlb --format roto
    python draft_agent.py --sport nba --format points --drafted "LeBron James,Luka Doncic"
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from external_stats import (
    get_nba_player_season_stats,
    get_nba_player_game_log,
    get_mlb_player_stats,
    get_mlb_spring_training_stats,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shipp API — Live Game Context
# ---------------------------------------------------------------------------

SHIPP_BASE_URL = "https://api.shipp.ai/api/v1"


class ShippLiveContext:
    """
    Thin Shipp client that fetches live game data to identify hot/cold players.
    Used as a supplementary signal during draft recommendations.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "fantasy-draft-assistant/1.0",
        })
        self.connection_id: Optional[str] = None
        self.last_event_id: Optional[str] = None

    def _url(self, path: str) -> str:
        """Build URL with api_key query parameter."""
        sep = "&" if "?" in path else "?"
        return f"{SHIPP_BASE_URL}{path}{sep}api_key={self.api_key}"

    def connect(self, sport: str) -> bool:
        """Create a Shipp connection for live game data."""
        filter_map = {
            "nba": "Track all NBA games today including scores, play-by-play, and player performance",
            "mlb": "Track all MLB games today including scores, play-by-play, and pitching changes",
        }
        try:
            resp = self.session.post(
                self._url("/connections/create"),
                json={
                    "filter_instructions": filter_map.get(sport, f"Track all {sport} games today with scores and play-by-play"),
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self.connection_id = data.get("connection_id") or data.get("id")
            return self.connection_id is not None
        except Exception as exc:
            logger.warning("Failed to create Shipp connection: %s", exc)
            return False

    def get_live_games(self) -> list[dict]:
        """Poll for current game states and events."""
        if not self.connection_id:
            return []

        payload = {}
        if self.last_event_id:
            payload["since_event_id"] = self.last_event_id

        try:
            resp = self.session.post(
                self._url(f"/connections/{self.connection_id}"),
                json=payload,
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()

            events = data.get("data", data.get("events", []))
            if events:
                last = events[-1]
                cursor = last.get("id") or last.get("event_id")
                if cursor:
                    self.last_event_id = str(cursor)

            return events
        except Exception as exc:
            logger.warning("Failed to poll Shipp: %s", exc)
            return []

    def extract_hot_players(self, games: list[dict]) -> dict[str, dict]:
        """
        Analyze live game data to identify players performing above average.
        Returns {player_name: {stat_key: value}} for notable performers.
        """
        hot_players = {}

        for game in games:
            status = str(game.get("status") or "").lower()
            if status not in ("live", "in_progress", "active", "in progress"):
                continue

            # Look for player stats in various response shapes
            for team_key in ("home_players", "away_players", "players",
                             "home_stats", "away_stats"):
                players = game.get(team_key) or []
                if isinstance(players, dict):
                    players = list(players.values())

                for player in players:
                    if not isinstance(player, dict):
                        continue
                    name = (
                        player.get("name")
                        or player.get("player_name")
                        or player.get("fullName")
                        or ""
                    )
                    if not name:
                        continue

                    pts = _safe_float(player.get("points") or player.get("pts"))
                    reb = _safe_float(player.get("rebounds") or player.get("reb"))
                    ast = _safe_float(player.get("assists") or player.get("ast"))

                    # Flag as "hot" if notable stats in an ongoing game
                    if pts >= 20 or reb >= 10 or ast >= 8:
                        hot_players[name] = {
                            "pts": pts,
                            "reb": reb,
                            "ast": ast,
                            "live": True,
                            "note": f"LIVE: {pts:.0f}pts/{reb:.0f}reb/{ast:.0f}ast tonight",
                        }

        return hot_players

    def close(self):
        if self.connection_id:
            try:
                self.session.post(
                    self._url(f"/connections/{self.connection_id}/close"),
                    json={},
                    timeout=5,
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Fantasy Scoring Models
# ---------------------------------------------------------------------------

# NBA Points League (standard ESPN-style)
NBA_POINTS_WEIGHTS = {
    "pts": 1.0,
    "reb": 1.2,
    "ast": 1.5,
    "stl": 3.0,
    "blk": 3.0,
    "fg3m": 0.5,
    "tov": -1.0,
}

# NBA Category League (9-cat) — value is based on z-scores across categories
NBA_CATEGORIES = ["pts", "reb", "ast", "stl", "blk", "fg_pct", "ft_pct", "fg3m", "tov"]

# MLB Points League (standard)
MLB_HITTING_WEIGHTS = {
    "runs": 1.0,
    "hr": 4.0,
    "rbi": 1.0,
    "sb": 2.0,
    "avg_bonus": 5.0,  # bonus per .010 above .270
}

MLB_PITCHING_WEIGHTS = {
    "wins": 5.0,
    "so": 1.0,
    "saves": 5.0,
    "era_bonus": -2.0,  # penalty per 0.50 above 3.50
    "whip_bonus": -3.0,  # penalty per 0.10 above 1.20
}

# ---------------------------------------------------------------------------
# Position Mapping
# ---------------------------------------------------------------------------

NBA_POSITIONS = {
    "PG": "Point Guard",
    "SG": "Shooting Guard",
    "SF": "Small Forward",
    "PF": "Power Forward",
    "C": "Center",
    "G": "Guard",
    "F": "Forward",
}

MLB_POSITIONS = {
    "C": "Catcher",
    "1B": "First Base",
    "2B": "Second Base",
    "3B": "Third Base",
    "SS": "Shortstop",
    "OF": "Outfield",
    "DH": "Designated Hitter",
    "SP": "Starting Pitcher",
    "RP": "Relief Pitcher",
}


# ---------------------------------------------------------------------------
# Player Ranking
# ---------------------------------------------------------------------------

@dataclass
class RankedPlayer:
    """A player with computed fantasy value and context."""
    name: str
    team: str
    position: str
    fantasy_value: float
    vor: float  # value over replacement
    season_stats: dict = field(default_factory=dict)
    recent_trend: float = 0.0  # % change in last 10 games vs season avg
    live_note: str = ""
    is_sleeper: bool = False
    sleeper_reason: str = ""
    recommendation: str = ""


def compute_nba_points_value(stats: dict) -> float:
    """Compute fantasy points per game for NBA points leagues."""
    if not stats:
        return 0.0
    total = 0.0
    for stat_key, weight in NBA_POINTS_WEIGHTS.items():
        total += _safe_float(stats.get(stat_key, 0)) * weight
    return round(total, 1)


def compute_mlb_hitting_value(stats: dict) -> float:
    """Compute fantasy value for MLB hitters."""
    if not stats:
        return 0.0
    total = 0.0
    for stat_key in ("runs", "hr", "rbi", "sb"):
        total += _safe_float(stats.get(stat_key, 0)) * MLB_HITTING_WEIGHTS.get(stat_key, 0)

    # Batting average bonus/penalty
    try:
        avg = float(stats.get("avg", ".250").lstrip(".") or "250") / 1000
    except (ValueError, TypeError):
        avg = 0.250
    if avg > 0.270:
        total += (avg - 0.270) * 1000 * MLB_HITTING_WEIGHTS["avg_bonus"]

    return round(total, 1)


def compute_mlb_pitching_value(stats: dict) -> float:
    """Compute fantasy value for MLB pitchers."""
    if not stats:
        return 0.0
    total = 0.0
    total += _safe_float(stats.get("wins", 0)) * MLB_PITCHING_WEIGHTS["wins"]
    total += _safe_float(stats.get("so", 0)) * MLB_PITCHING_WEIGHTS["so"]
    total += _safe_float(stats.get("saves", 0)) * MLB_PITCHING_WEIGHTS["saves"]

    # ERA penalty
    try:
        era = float(stats.get("era", "3.50"))
    except (ValueError, TypeError):
        era = 3.50
    if era > 3.50:
        total += (era - 3.50) / 0.50 * MLB_PITCHING_WEIGHTS["era_bonus"]

    # WHIP penalty
    try:
        whip = float(stats.get("whip", "1.20"))
    except (ValueError, TypeError):
        whip = 1.20
    if whip > 1.20:
        total += (whip - 1.20) / 0.10 * MLB_PITCHING_WEIGHTS["whip_bonus"]

    return round(total, 1)


def compute_recent_trend(season_stats: dict, game_log: list[dict]) -> float:
    """
    Calculate percentage change between recent game log average and season average.
    Positive = trending up, negative = trending down.
    """
    if not game_log or not season_stats:
        return 0.0

    season_ppg = _safe_float(season_stats.get("pts", 0))
    if season_ppg == 0:
        return 0.0

    recent_ppg = sum(_safe_float(g.get("pts", 0)) for g in game_log) / len(game_log)
    trend = ((recent_ppg - season_ppg) / season_ppg) * 100
    return round(trend, 1)


def identify_sleeper(
    stats: dict,
    trend: float,
    fantasy_value: float,
    position: str,
) -> tuple[bool, str]:
    """
    Determine if a player qualifies as a sleeper pick.
    Sleepers have recent upward trends that suggest breakout potential.
    """
    if trend >= 15.0 and fantasy_value > 20:
        return True, f"Trending +{trend:.0f}% over last 10 games"

    # High blocks/steals for their position
    blk = _safe_float(stats.get("blk", 0))
    stl = _safe_float(stats.get("stl", 0))
    if position in ("C", "PF") and blk >= 2.0 and trend >= 5:
        return True, f"Elite blocks ({blk:.1f}/g) with upward trend"
    if position in ("PG", "SG") and stl >= 1.8 and trend >= 5:
        return True, f"Elite steals ({stl:.1f}/g) with upward trend"

    # MLB: spring training standout
    if stats.get("type") == "spring_training":
        try:
            avg = float(stats.get("avg", ".000").lstrip(".") or "0") / 1000
        except (ValueError, TypeError):
            avg = 0.0
        if avg >= 0.350 and _safe_float(stats.get("games", 0)) >= 5:
            return True, f"Spring training breakout ({stats.get('avg', '')} AVG)"

    return False, ""


def compute_vor(
    player_value: float,
    position: str,
    replacement_values: dict[str, float],
) -> float:
    """Compute value over replacement for a position."""
    replacement = replacement_values.get(position, 0)
    return round(player_value - replacement, 1)


# ---------------------------------------------------------------------------
# Draft Board Builder
# ---------------------------------------------------------------------------

# Default NBA player pool for demonstration
NBA_DEFAULT_POOL = [
    "Nikola Jokic", "Luka Doncic", "Shai Gilgeous-Alexander",
    "Jayson Tatum", "Anthony Edwards", "Victor Wembanyama",
    "Tyrese Haliburton", "Domantas Sabonis", "LeBron James",
    "Kevin Durant", "Damian Lillard", "Devin Booker",
    "Anthony Davis", "Trae Young", "Bam Adebayo",
    "De'Aaron Fox", "Donovan Mitchell", "Jalen Brunson",
    "Jaren Jackson Jr", "Chet Holmgren", "Paolo Banchero",
    "Scottie Barnes", "Darius Garland", "Tyler Herro",
    "Lauri Markkanen", "Franz Wagner", "Cade Cunningham",
    "Tyrese Maxey", "Desmond Bane", "Dejounte Murray",
]

MLB_DEFAULT_POOL = [
    "Shohei Ohtani", "Aaron Judge", "Ronald Acuna Jr",
    "Mookie Betts", "Freddie Freeman", "Trea Turner",
    "Juan Soto", "Corey Seager", "Bobby Witt Jr",
    "Julio Rodriguez", "Corbin Carroll", "Gunnar Henderson",
    "Elly De La Cruz", "Marcus Semien", "Vladimir Guerrero Jr",
    "Spencer Strider", "Zack Wheeler", "Gerrit Cole",
    "Corbin Burnes", "Yoshinobu Yamamoto", "Dylan Cease",
    "Logan Webb", "Bryce Harper", "Matt Olson",
    "Pete Alonso", "Bo Bichette", "Jose Ramirez",
    "Kyle Tucker", "Adley Rutschman", "Jackson Chourio",
]


def build_draft_board(
    sport: str,
    scoring_format: str,
    drafted_names: list[str],
    roster: list[str],
    live_context: Optional[ShippLiveContext] = None,
) -> list[RankedPlayer]:
    """
    Build a ranked draft board of available players.

    Args:
        sport: "nba" or "mlb"
        scoring_format: "points", "categories", or "roto"
        drafted_names: Players already drafted (unavailable)
        roster: Your current roster (for positional needs)
        live_context: Optional Shipp connection for live game signals

    Returns:
        List of RankedPlayer sorted by adjusted fantasy value.
    """
    drafted_lower = {n.lower() for n in drafted_names}

    # Get live game signals if available
    hot_players = {}
    if live_context:
        games = live_context.get_live_games()
        hot_players = live_context.extract_hot_players(games)

    # Determine player pool
    pool = NBA_DEFAULT_POOL if sport == "nba" else MLB_DEFAULT_POOL

    # Filter out drafted players
    available = [
        name for name in pool
        if name.lower() not in drafted_lower
    ]

    ranked = []
    replacement_values: dict[str, list[float]] = {}

    print(f"\nAnalyzing {len(available)} available players...")

    for i, player_name in enumerate(available):
        print(f"  [{i+1}/{len(available)}] {player_name}", end="\r")

        if sport == "nba":
            stats = get_nba_player_season_stats(player_name)
            if not stats:
                continue

            if scoring_format == "points":
                value = compute_nba_points_value(stats)
            else:
                # For categories, use a simplified composite
                value = compute_nba_points_value(stats)

            # Get recent game log for trend
            player_id = stats.get("player_id")
            trend = 0.0
            if player_id:
                game_log = get_nba_player_game_log(player_id, last_n=10)
                trend = compute_recent_trend(stats, game_log)

            position = stats.get("position", "")

        elif sport == "mlb":
            # Check if pitcher or hitter based on position
            stats = get_mlb_player_stats(player_name, stat_group="hitting")
            if not stats:
                stats = get_mlb_player_stats(player_name, stat_group="pitching")
                if not stats:
                    continue
                value = compute_mlb_pitching_value(stats)
            else:
                value = compute_mlb_hitting_value(stats)

            # Also check spring training stats
            spring = get_mlb_spring_training_stats(player_name)
            if spring and spring.get("games"):
                stats["spring_training"] = spring

            position = stats.get("position", "")
            trend = 0.0  # MLB trend requires different calculation

        else:
            continue

        # Track values per position for replacement calculation
        pos_key = position.split("/")[0] if position else "UTIL"
        replacement_values.setdefault(pos_key, []).append(value)

        # Check for live context
        live_note = ""
        live_bonus = 0.0
        if player_name in hot_players:
            hp = hot_players[player_name]
            live_note = hp.get("note", "")
            live_bonus = 3.0  # small boost for active hot performers

        # Check sleeper status
        is_sleeper, sleeper_reason = identify_sleeper(
            stats, trend, value, position
        )

        adjusted_value = value + live_bonus
        if trend > 10:
            adjusted_value += trend * 0.1  # small trend boost

        rp = RankedPlayer(
            name=player_name,
            team=stats.get("team", ""),
            position=position,
            fantasy_value=adjusted_value,
            vor=0.0,  # computed after all players processed
            season_stats=stats,
            recent_trend=trend,
            live_note=live_note,
            is_sleeper=is_sleeper,
            sleeper_reason=sleeper_reason,
        )
        ranked.append(rp)

    # Compute replacement values (average of players ranked 8-12 at each position)
    repl = {}
    for pos, values in replacement_values.items():
        sorted_vals = sorted(values, reverse=True)
        if len(sorted_vals) >= 12:
            repl[pos] = sum(sorted_vals[7:12]) / 5
        elif len(sorted_vals) >= 3:
            repl[pos] = sorted_vals[-1]
        else:
            repl[pos] = 0

    # Apply VOR
    for rp in ranked:
        pos_key = rp.position.split("/")[0] if rp.position else "UTIL"
        rp.vor = compute_vor(rp.fantasy_value, pos_key, repl)

    # Positional need boost
    roster_positions = _count_roster_positions(roster, sport)
    for rp in ranked:
        pos_key = rp.position.split("/")[0] if rp.position else "UTIL"
        if roster_positions.get(pos_key, 0) == 0:
            rp.vor += 2.0  # positional need bonus
            rp.recommendation = f"fills {pos_key} need"

    # Sort by VOR descending
    ranked.sort(key=lambda p: p.vor, reverse=True)

    # Add recommendation to top pick if not already set
    if ranked and not ranked[0].recommendation:
        ranked[0].recommendation = "BEST AVAILABLE"

    print(f"\n  Done. {len(ranked)} players ranked.\n")
    return ranked


def _count_roster_positions(roster_names: list[str], sport: str) -> dict[str, int]:
    """Count how many of each position are on the roster."""
    # In a real implementation, this would look up positions from the API
    # For now, return empty dict (all positions needed)
    return {}


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------


def format_draft_board(
    ranked: list[RankedPlayer],
    sport: str,
    scoring_format: str,
    top_n: int = 15,
) -> str:
    """Format the ranked players as a printable draft board."""
    lines = []
    sport_label = "NBA" if sport == "nba" else "MLB"
    format_label = scoring_format.title()

    lines.append("=" * 72)
    lines.append(f"  FANTASY DRAFT ASSISTANT -- {sport_label} {format_label} League")
    lines.append("=" * 72)
    lines.append("")
    lines.append("  BEST AVAILABLE PLAYERS")
    lines.append("  " + "-" * 68)

    for i, rp in enumerate(ranked[:top_n], 1):
        # Rank and name
        tag = ""
        if rp.is_sleeper:
            tag = " [SLEEPER]"
        if rp.live_note:
            tag += " [LIVE]"

        lines.append(
            f"  {i:2d}. {rp.name} ({rp.position}, {rp.team})"
            f" -- VOR: {rp.vor:+.1f}{tag}"
        )

        # Season stats summary
        if sport == "nba":
            s = rp.season_stats
            lines.append(
                f"      Season: {s.get('pts', 0):.1f}pts, "
                f"{s.get('reb', 0):.1f}reb, {s.get('ast', 0):.1f}ast, "
                f"{s.get('stl', 0):.1f}stl, {s.get('blk', 0):.1f}blk"
            )
        elif sport == "mlb":
            s = rp.season_stats
            if s.get("era") is not None and "wins" in s:
                lines.append(
                    f"      Season: {s.get('wins', 0)}W-{s.get('losses', 0)}L, "
                    f"{s.get('era', '0.00')} ERA, {s.get('so', 0)} K, "
                    f"{s.get('whip', '0.00')} WHIP"
                )
            else:
                lines.append(
                    f"      Season: {s.get('avg', '.000')} AVG, "
                    f"{s.get('hr', 0)} HR, {s.get('rbi', 0)} RBI, "
                    f"{s.get('runs', 0)} R, {s.get('sb', 0)} SB"
                )

        # Trend
        if rp.recent_trend != 0:
            direction = "UP" if rp.recent_trend > 0 else "DOWN"
            lines.append(
                f"      Last 10: {direction} {abs(rp.recent_trend):.0f}% from season avg"
            )

        # Live note
        if rp.live_note:
            lines.append(f"      {rp.live_note}")

        # Sleeper reason
        if rp.is_sleeper and rp.sleeper_reason:
            lines.append(f"      >> SLEEPER: {rp.sleeper_reason}")

        # Recommendation
        if rp.recommendation:
            lines.append(f"      >> {rp.recommendation}")

        lines.append("")

    # Positional scarcity summary
    lines.append("  " + "-" * 68)
    lines.append("  POSITIONAL SCARCITY")
    lines.append("")

    positions = NBA_POSITIONS if sport == "nba" else MLB_POSITIONS
    for pos_abbr, pos_name in positions.items():
        count = sum(
            1 for rp in ranked
            if pos_abbr in (rp.position or "").split("/")
        )
        if count == 0:
            continue
        label = "SCARCE" if count <= 3 else "THIN" if count <= 6 else "DEEP"
        lines.append(f"    {pos_abbr:3s}: {count:2d} quality options ({label})")

    lines.append("")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value) -> float:
    """Safely convert to float, default 0."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Fantasy Draft Assistant — real-time draft recommendations"
    )
    parser.add_argument(
        "--sport",
        type=str,
        choices=["nba", "mlb"],
        required=True,
        help="Sport to draft for",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["points", "categories", "roto"],
        default="points",
        dest="scoring_format",
        help="Scoring format (default: points)",
    )
    parser.add_argument(
        "--drafted",
        type=str,
        default="",
        help="Comma-separated list of already-drafted players",
    )
    parser.add_argument(
        "--roster",
        type=str,
        default="",
        help="Comma-separated list of your current roster",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        help="Number of recommendations to show (default: 15)",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Skip live game context (faster, offline-friendly)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    drafted = [n.strip() for n in args.drafted.split(",") if n.strip()]
    roster = [n.strip() for n in args.roster.split(",") if n.strip()]

    # Set up live context if available and requested
    live_ctx = None
    if not args.no_live:
        api_key = os.environ.get("SHIPP_API_KEY", "")
        if api_key:
            print("Connecting to live game feed...")
            live_ctx = ShippLiveContext(api_key)
            if live_ctx.connect(args.sport):
                print("  Connected. Live game context active.")
            else:
                print("  No live connection. Proceeding with stats only.")
                live_ctx = None
        else:
            print(
                "No SHIPP_API_KEY set. Running without live context.\n"
                "Set your key for real-time game signals: "
                "export SHIPP_API_KEY='your-key'\n"
                "Get a free key at: https://platform.shipp.ai\n"
            )

    try:
        # Build the draft board
        ranked = build_draft_board(
            sport=args.sport,
            scoring_format=args.scoring_format,
            drafted_names=drafted,
            roster=roster,
            live_context=live_ctx,
        )

        if not ranked:
            print("\nNo player data available. Check your internet connection.")
            sys.exit(1)

        # Output the board
        board = format_draft_board(
            ranked,
            sport=args.sport,
            scoring_format=args.scoring_format,
            top_n=args.top,
        )
        print(board)

    finally:
        if live_ctx:
            live_ctx.close()


if __name__ == "__main__":
    main()

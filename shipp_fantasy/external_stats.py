"""
External stats integrations for Fantasy Draft Assistant.

Fetches player and season data from free public APIs:
  - balldontlie.io — NBA player stats, season averages, game logs
  - statsapi.mlb.com — MLB player stats, spring training, rosters

All functions return dicts with normalized keys. On failure, functions return
empty dicts or lists rather than raising — the draft agent handles missing data
gracefully.
"""

import logging
import time
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# balldontlie.io — NBA Stats (no API key required)
# Rate limit: 30 requests/minute
# ---------------------------------------------------------------------------

BDL_BASE_URL = "https://api.balldontlie.io/v1"
BDL_RATE_LIMIT_DELAY = 2.1  # seconds between requests to stay under 30/min
_bdl_last_request = 0.0


def _bdl_request(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """Make a rate-limited request to balldontlie API."""
    global _bdl_last_request
    now = time.time()
    elapsed = now - _bdl_last_request
    if elapsed < BDL_RATE_LIMIT_DELAY:
        time.sleep(BDL_RATE_LIMIT_DELAY - elapsed)

    url = f"{BDL_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(url, params=params or {}, timeout=10)
        _bdl_last_request = time.time()
        if resp.status_code == 429:
            logger.warning("balldontlie rate limit hit. Backing off 60s.")
            time.sleep(60)
            return None
        if resp.status_code != 200:
            logger.warning(
                "balldontlie %s returned %d: %s",
                endpoint,
                resp.status_code,
                resp.text[:200],
            )
            return None
        return resp.json()
    except requests.RequestException as exc:
        logger.error("balldontlie request failed: %s", exc)
        return None


def _find_bdl_player(player_name: str) -> Optional[dict]:
    """Search for a player by name on balldontlie. Returns first match."""
    data = _bdl_request("players", {"search": player_name, "per_page": 5})
    if not data or not data.get("data"):
        return None
    # Try exact match first, then partial
    candidates = data["data"]
    name_lower = player_name.lower()
    for p in candidates:
        full = f"{p.get('first_name', '')} {p.get('last_name', '')}".lower()
        if full == name_lower:
            return p
    return candidates[0]  # best guess


def get_nba_player_season_stats(player_name: str, season: int = 2025) -> dict:
    """
    Get a player's season averages for the specified season.

    Args:
        player_name: Full player name, e.g. "LeBron James"
        season: Season year (2025 = 2025-26 season)

    Returns:
        Dict with keys: player_id, name, team, games_played, pts, reb, ast,
        stl, blk, fg_pct, ft_pct, fg3_pct, fg3m, tov, min
        Returns empty dict on failure.
    """
    player = _find_bdl_player(player_name)
    if not player:
        logger.info("NBA player not found: %s", player_name)
        return {}

    player_id = player.get("id")
    data = _bdl_request(
        "season_averages",
        {"season": season, "player_ids[]": player_id},
    )

    if not data or not data.get("data"):
        # Try previous season as fallback
        data = _bdl_request(
            "season_averages",
            {"season": season - 1, "player_ids[]": player_id},
        )
        if not data or not data.get("data"):
            return {}

    avg = data["data"][0]
    team = player.get("team", {})

    return {
        "player_id": player_id,
        "name": f"{player.get('first_name', '')} {player.get('last_name', '')}",
        "team": team.get("abbreviation") or team.get("full_name", ""),
        "position": player.get("position", ""),
        "games_played": avg.get("games_played", 0),
        "pts": avg.get("pts", 0.0),
        "reb": avg.get("reb", 0.0),
        "ast": avg.get("ast", 0.0),
        "stl": avg.get("stl", 0.0),
        "blk": avg.get("blk", 0.0),
        "tov": avg.get("turnover", 0.0),
        "fg_pct": avg.get("fg_pct", 0.0),
        "ft_pct": avg.get("ft_pct", 0.0),
        "fg3_pct": avg.get("fg3_pct", 0.0),
        "fg3m": avg.get("fg3m", 0.0),
        "min": avg.get("min", "0"),
    }


def get_nba_player_game_log(player_id: int, last_n: int = 10) -> list[dict]:
    """
    Get a player's recent game log.

    Args:
        player_id: balldontlie player ID
        last_n: Number of recent games to return (max 25)

    Returns:
        List of game dicts with keys: date, opponent, pts, reb, ast, stl,
        blk, fg_pct, min, result
        Returns empty list on failure.
    """
    data = _bdl_request(
        "stats",
        {
            "player_ids[]": player_id,
            "per_page": min(last_n, 25),
            "sort": "-game.date",
        },
    )

    if not data or not data.get("data"):
        return []

    games = []
    for stat in data["data"][:last_n]:
        game_info = stat.get("game", {})
        team = stat.get("team", {})
        team_abbr = team.get("abbreviation", "")

        # Determine opponent
        home = game_info.get("home_team_id")
        away = game_info.get("visitor_team_id")
        team_id = team.get("id")
        is_home = team_id == home
        opponent_id = away if is_home else home

        games.append({
            "date": game_info.get("date", ""),
            "pts": stat.get("pts", 0),
            "reb": stat.get("reb", 0),
            "ast": stat.get("ast", 0),
            "stl": stat.get("stl", 0),
            "blk": stat.get("blk", 0),
            "tov": stat.get("turnover", 0),
            "fg_pct": stat.get("fg_pct", 0.0),
            "min": stat.get("min", "0"),
            "is_home": is_home,
        })

    return games


def get_nba_all_players(season: int = 2025, per_page: int = 100) -> list[dict]:
    """
    Get all active NBA players for building a draft pool.
    Returns list of basic player info dicts.
    """
    players = []
    page = 1
    max_pages = 5  # limit to avoid excessive requests

    while page <= max_pages:
        data = _bdl_request(
            "players",
            {"per_page": per_page, "page": page},
        )
        if not data or not data.get("data"):
            break

        for p in data["data"]:
            players.append({
                "id": p.get("id"),
                "name": f"{p.get('first_name', '')} {p.get('last_name', '')}",
                "position": p.get("position", ""),
                "team": (p.get("team") or {}).get("abbreviation", ""),
            })

        meta = data.get("meta", {})
        if page >= meta.get("total_pages", 1):
            break
        page += 1

    return players


# ---------------------------------------------------------------------------
# MLB Stats API — statsapi.mlb.com (no API key required)
# No strict rate limit but be respectful
# ---------------------------------------------------------------------------

MLB_BASE_URL = "https://statsapi.mlb.com/api/v1"
MLB_RATE_DELAY = 0.5  # seconds between requests
_mlb_last_request = 0.0


def _mlb_request(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    """Make a rate-limited request to MLB Stats API."""
    global _mlb_last_request
    now = time.time()
    elapsed = now - _mlb_last_request
    if elapsed < MLB_RATE_DELAY:
        time.sleep(MLB_RATE_DELAY - elapsed)

    url = f"{MLB_BASE_URL}/{endpoint}"
    try:
        resp = requests.get(url, params=params or {}, timeout=10)
        _mlb_last_request = time.time()
        if resp.status_code != 200:
            logger.warning(
                "MLB API %s returned %d: %s",
                endpoint,
                resp.status_code,
                resp.text[:200],
            )
            return None
        return resp.json()
    except requests.RequestException as exc:
        logger.error("MLB API request failed: %s", exc)
        return None


def _find_mlb_player(player_name: str) -> Optional[dict]:
    """Search for an MLB player by name. Returns first match."""
    data = _mlb_request(
        "sports/1/players",
        {"season": 2026, "search": player_name},
    )
    if not data:
        # Fallback to people search
        data = _mlb_request(
            "people/search",
            {"names": player_name, "sportId": 1},
        )

    if not data:
        return None

    # Navigate the response structure
    people = data.get("people") or data.get("row") or []
    if not people:
        # Try nested under players
        players = data.get("players", [])
        if players:
            people = players

    if not people:
        return None

    name_lower = player_name.lower()
    for p in people:
        full = p.get("fullName", "").lower()
        if full == name_lower:
            return p
    return people[0] if people else None


def get_mlb_player_stats(
    player_name: str,
    season: int = 2025,
    stat_group: str = "hitting",
) -> dict:
    """
    Get an MLB player's season stats.

    Args:
        player_name: Full name, e.g. "Aaron Judge"
        season: Stats season year
        stat_group: "hitting" or "pitching"

    Returns:
        Dict with normalized stat keys. Empty dict on failure.
    """
    player = _find_mlb_player(player_name)
    if not player:
        logger.info("MLB player not found: %s", player_name)
        return {}

    player_id = player.get("id")
    if not player_id:
        return {}

    data = _mlb_request(
        f"people/{player_id}/stats",
        {
            "stats": "season",
            "season": season,
            "group": stat_group,
            "sportId": 1,
        },
    )

    if not data:
        return {}

    stats_list = data.get("stats", [])
    if not stats_list:
        return {}

    splits = stats_list[0].get("splits", [])
    if not splits:
        return {}

    raw = splits[0].get("stat", {})
    position = player.get("primaryPosition", {}).get("abbreviation", "")

    result = {
        "player_id": player_id,
        "name": player.get("fullName", player_name),
        "team": player.get("currentTeam", {}).get("name", ""),
        "position": position,
    }

    if stat_group == "hitting":
        result.update({
            "games": raw.get("gamesPlayed", 0),
            "avg": raw.get("avg", ".000"),
            "hr": raw.get("homeRuns", 0),
            "rbi": raw.get("rbi", 0),
            "runs": raw.get("runs", 0),
            "sb": raw.get("stolenBases", 0),
            "obp": raw.get("obp", ".000"),
            "slg": raw.get("slg", ".000"),
            "ops": raw.get("ops", ".000"),
            "hits": raw.get("hits", 0),
            "ab": raw.get("atBats", 0),
            "bb": raw.get("baseOnBalls", 0),
            "so": raw.get("strikeOuts", 0),
        })
    elif stat_group == "pitching":
        result.update({
            "games": raw.get("gamesPlayed", 0),
            "wins": raw.get("wins", 0),
            "losses": raw.get("losses", 0),
            "era": raw.get("era", "0.00"),
            "whip": raw.get("whip", "0.00"),
            "so": raw.get("strikeOuts", 0),
            "ip": raw.get("inningsPitched", "0.0"),
            "saves": raw.get("saves", 0),
            "holds": raw.get("holds", 0),
            "bb": raw.get("baseOnBalls", 0),
            "hits_allowed": raw.get("hits", 0),
            "hr_allowed": raw.get("homeRuns", 0),
        })

    return result


def get_mlb_spring_training_stats(player_name: str) -> dict:
    """
    Get a player's spring training stats for the current year.
    Spring training uses gameType=S.

    Returns:
        Dict with spring training stats. Empty dict if not available.
    """
    player = _find_mlb_player(player_name)
    if not player:
        return {}

    player_id = player.get("id")
    if not player_id:
        return {}

    position = player.get("primaryPosition", {}).get("abbreviation", "")
    is_pitcher = position in ("P", "SP", "RP", "CL")
    stat_group = "pitching" if is_pitcher else "hitting"

    # Spring training stats via gameType filter
    data = _mlb_request(
        f"people/{player_id}/stats",
        {
            "stats": "season",
            "season": 2026,
            "group": stat_group,
            "gameType": "S",
            "sportId": 1,
        },
    )

    if not data:
        return {}

    stats_list = data.get("stats", [])
    if not stats_list:
        return {}

    splits = stats_list[0].get("splits", [])
    if not splits:
        # Spring training hasn't started or no stats yet
        return {
            "name": player.get("fullName", player_name),
            "position": position,
            "note": "No spring training stats available yet",
        }

    raw = splits[0].get("stat", {})

    result = {
        "player_id": player_id,
        "name": player.get("fullName", player_name),
        "team": player.get("currentTeam", {}).get("name", ""),
        "position": position,
        "type": "spring_training",
        "season": 2026,
    }

    if stat_group == "hitting":
        result.update({
            "games": raw.get("gamesPlayed", 0),
            "avg": raw.get("avg", ".000"),
            "hr": raw.get("homeRuns", 0),
            "rbi": raw.get("rbi", 0),
            "runs": raw.get("runs", 0),
            "hits": raw.get("hits", 0),
            "ab": raw.get("atBats", 0),
            "ops": raw.get("ops", ".000"),
        })
    else:
        result.update({
            "games": raw.get("gamesPlayed", 0),
            "era": raw.get("era", "0.00"),
            "ip": raw.get("inningsPitched", "0.0"),
            "so": raw.get("strikeOuts", 0),
            "bb": raw.get("baseOnBalls", 0),
            "whip": raw.get("whip", "0.00"),
        })

    return result


def get_mlb_team_roster(team_id: int, season: int = 2026) -> list[dict]:
    """
    Get a team's 40-man roster.

    Args:
        team_id: MLB team ID
        season: Roster season

    Returns:
        List of player dicts with id, name, position, jersey_number.
    """
    data = _mlb_request(
        f"teams/{team_id}/roster",
        {"rosterType": "40Man", "season": season},
    )

    if not data or not data.get("roster"):
        return []

    roster = []
    for entry in data["roster"]:
        person = entry.get("person", {})
        pos = entry.get("position", {})
        roster.append({
            "id": person.get("id"),
            "name": person.get("fullName", ""),
            "position": pos.get("abbreviation", ""),
            "jersey_number": entry.get("jerseyNumber", ""),
            "status": entry.get("status", {}).get("description", ""),
        })

    return roster


def get_mlb_prospect_rankings() -> list[dict]:
    """
    Get MLB top prospects from the draft prospects endpoint.
    Returns list of prospect dicts with basic info.
    """
    data = _mlb_request(
        "draft/prospects",
        {"year": 2026, "limit": 50, "sportId": 1},
    )

    if not data or not data.get("prospects"):
        # Fallback: try pipeline rankings
        return []

    prospects = []
    for p in data.get("prospects", [])[:50]:
        person = p.get("person", {})
        prospects.append({
            "rank": p.get("rank", 0),
            "name": person.get("fullName", ""),
            "position": person.get("primaryPosition", {}).get("abbreviation", ""),
            "team": p.get("team", {}).get("name", ""),
            "age": person.get("currentAge"),
        })

    return prospects

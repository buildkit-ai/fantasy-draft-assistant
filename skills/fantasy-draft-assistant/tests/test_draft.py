"""
Comprehensive tests for the Fantasy Draft Assistant.

Tests cover:
  - external_stats: NBA/MLB stats fetching, rate limiting, error handling
  - draft_agent: scoring models, VOR calculation, sleeper detection,
                 ShippLiveContext, draft board building, formatting
"""

import sys
import os
import time
from unittest import mock
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

# Ensure the scripts directory is on sys.path so that `external_stats` and
# `draft_agent` can be imported the same way the production code does.
SCRIPTS_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "scripts"
)
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import external_stats  # noqa: E402
import draft_agent      # noqa: E402

from external_stats import (
    _bdl_request,
    _find_bdl_player,
    get_nba_player_season_stats,
    get_nba_player_game_log,
    get_nba_all_players,
    _mlb_request,
    _find_mlb_player,
    get_mlb_player_stats,
    get_mlb_spring_training_stats,
    get_mlb_team_roster,
    get_mlb_prospect_rankings,
)

from draft_agent import (
    ShippLiveContext,
    RankedPlayer,
    compute_nba_points_value,
    compute_mlb_hitting_value,
    compute_mlb_pitching_value,
    compute_recent_trend,
    identify_sleeper,
    compute_vor,
    build_draft_board,
    format_draft_board,
    _safe_float,
    _count_roster_positions,
    NBA_POINTS_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


SAMPLE_BDL_PLAYER = {
    "id": 237,
    "first_name": "LeBron",
    "last_name": "James",
    "position": "F",
    "team": {"id": 14, "abbreviation": "LAL", "full_name": "Los Angeles Lakers"},
}

SAMPLE_BDL_SEASON_AVG = {
    "games_played": 55,
    "pts": 25.7,
    "reb": 7.3,
    "ast": 8.3,
    "stl": 1.3,
    "blk": 0.5,
    "turnover": 3.5,
    "fg_pct": 0.540,
    "ft_pct": 0.750,
    "fg3_pct": 0.410,
    "fg3m": 2.1,
    "min": "35.5",
}

SAMPLE_MLB_PLAYER = {
    "id": 660271,
    "fullName": "Aaron Judge",
    "primaryPosition": {"abbreviation": "OF"},
    "currentTeam": {"name": "New York Yankees"},
}


# ---------------------------------------------------------------------------
# 1. external_stats — BDL (NBA) request layer
# ---------------------------------------------------------------------------


class TestBdlRequest:
    """Tests for _bdl_request rate-limiting and error paths."""

    @patch("external_stats.requests.get")
    @patch("external_stats.time.sleep")
    @patch("external_stats.time.time")
    def test_bdl_request_success(self, mock_time, mock_sleep, mock_get):
        """Successful BDL request returns parsed JSON."""
        # Make sure no rate-limit sleep is triggered
        mock_time.return_value = 1e9
        external_stats._bdl_last_request = 0.0

        mock_get.return_value = _mock_response(200, {"data": [1, 2, 3]})
        result = _bdl_request("players", {"search": "LeBron"})

        assert result == {"data": [1, 2, 3]}
        mock_get.assert_called_once()

    @patch("external_stats.requests.get")
    @patch("external_stats.time.sleep")
    @patch("external_stats.time.time")
    def test_bdl_request_rate_limit_429(self, mock_time, mock_sleep, mock_get):
        """429 response triggers a 60-second back-off and returns None."""
        mock_time.return_value = 1e9
        external_stats._bdl_last_request = 0.0

        mock_get.return_value = _mock_response(429, text="Rate limited")
        result = _bdl_request("players")

        assert result is None
        # Should have called sleep(60) for the 429 back-off
        mock_sleep.assert_called_with(60)

    @patch("external_stats.requests.get")
    @patch("external_stats.time.sleep")
    @patch("external_stats.time.time")
    def test_bdl_request_non_200(self, mock_time, mock_sleep, mock_get):
        """Non-200, non-429 status returns None without back-off."""
        mock_time.return_value = 1e9
        external_stats._bdl_last_request = 0.0

        mock_get.return_value = _mock_response(500, text="Internal Server Error")
        result = _bdl_request("season_averages")

        assert result is None

    @patch("external_stats.requests.get")
    @patch("external_stats.time.sleep")
    @patch("external_stats.time.time")
    def test_bdl_request_network_exception(self, mock_time, mock_sleep, mock_get):
        """Network failure returns None."""
        mock_time.return_value = 1e9
        external_stats._bdl_last_request = 0.0

        import requests as real_requests
        mock_get.side_effect = real_requests.ConnectionError("DNS failure")
        result = _bdl_request("players")

        assert result is None


# ---------------------------------------------------------------------------
# 2. external_stats — NBA player search & stats
# ---------------------------------------------------------------------------


class TestNbaPlayerSearch:
    """Tests for _find_bdl_player and get_nba_player_season_stats."""

    @patch("external_stats._bdl_request")
    def test_find_bdl_player_exact_match(self, mock_req):
        mock_req.return_value = {
            "data": [
                {"first_name": "LeBron", "last_name": "James", "id": 237},
                {"first_name": "LeBron", "last_name": "Jameson", "id": 999},
            ]
        }
        result = _find_bdl_player("LeBron James")
        assert result["id"] == 237

    @patch("external_stats._bdl_request")
    def test_find_bdl_player_no_results(self, mock_req):
        mock_req.return_value = {"data": []}
        result = _find_bdl_player("Unknown Player")
        assert result is None

    @patch("external_stats._bdl_request")
    def test_find_bdl_player_none_response(self, mock_req):
        mock_req.return_value = None
        result = _find_bdl_player("LeBron James")
        assert result is None

    @patch("external_stats._bdl_request")
    def test_get_nba_season_stats_success(self, mock_req):
        """Full success path: player found and season averages returned."""
        mock_req.side_effect = [
            # First call: player search
            {"data": [SAMPLE_BDL_PLAYER]},
            # Second call: season averages
            {"data": [SAMPLE_BDL_SEASON_AVG]},
        ]
        stats = get_nba_player_season_stats("LeBron James", season=2025)

        assert stats["name"] == "LeBron James"
        assert stats["pts"] == 25.7
        assert stats["team"] == "LAL"

    @patch("external_stats._bdl_request")
    def test_get_nba_season_stats_fallback_to_previous_season(self, mock_req):
        """When current season has no data, falls back to previous season."""
        mock_req.side_effect = [
            # Player search
            {"data": [SAMPLE_BDL_PLAYER]},
            # Current season: empty
            {"data": []},
            # Fallback (season-1): has data
            {"data": [SAMPLE_BDL_SEASON_AVG]},
        ]
        stats = get_nba_player_season_stats("LeBron James", season=2025)

        assert stats["pts"] == 25.7
        # Verify three requests were made (search + current + fallback)
        assert mock_req.call_count == 3

    @patch("external_stats._bdl_request")
    def test_get_nba_season_stats_player_not_found(self, mock_req):
        """Player not found returns empty dict."""
        mock_req.return_value = {"data": []}
        stats = get_nba_player_season_stats("Nonexistent Player")
        assert stats == {}


# ---------------------------------------------------------------------------
# 3. external_stats — NBA game log
# ---------------------------------------------------------------------------


class TestNbaGameLog:

    @patch("external_stats._bdl_request")
    def test_get_game_log_success(self, mock_req):
        mock_req.return_value = {
            "data": [
                {
                    "pts": 30, "reb": 8, "ast": 10, "stl": 2, "blk": 1,
                    "turnover": 3, "fg_pct": 0.55, "min": "38",
                    "game": {"date": "2025-12-01", "home_team_id": 14, "visitor_team_id": 5},
                    "team": {"id": 14, "abbreviation": "LAL"},
                }
            ]
        }
        log = get_nba_player_game_log(237, last_n=5)

        assert len(log) == 1
        assert log[0]["pts"] == 30
        assert log[0]["is_home"] is True

    @patch("external_stats._bdl_request")
    def test_get_game_log_empty(self, mock_req):
        mock_req.return_value = None
        log = get_nba_player_game_log(237)
        assert log == []


# ---------------------------------------------------------------------------
# 4. external_stats — MLB request layer & player search
# ---------------------------------------------------------------------------


class TestMlbApis:

    @patch("external_stats.requests.get")
    @patch("external_stats.time.sleep")
    @patch("external_stats.time.time")
    def test_mlb_request_success(self, mock_time, mock_sleep, mock_get):
        mock_time.return_value = 1e9
        external_stats._mlb_last_request = 0.0

        mock_get.return_value = _mock_response(200, {"people": [SAMPLE_MLB_PLAYER]})
        result = _mlb_request("sports/1/players", {"season": 2026})

        assert result["people"][0]["fullName"] == "Aaron Judge"

    @patch("external_stats.requests.get")
    @patch("external_stats.time.sleep")
    @patch("external_stats.time.time")
    def test_mlb_request_failure(self, mock_time, mock_sleep, mock_get):
        mock_time.return_value = 1e9
        external_stats._mlb_last_request = 0.0

        import requests as real_requests
        mock_get.side_effect = real_requests.Timeout("Request timed out")
        result = _mlb_request("people/1/stats")
        assert result is None

    @patch("external_stats._mlb_request")
    def test_find_mlb_player_exact_match(self, mock_req):
        mock_req.return_value = {"people": [SAMPLE_MLB_PLAYER]}
        result = _find_mlb_player("Aaron Judge")
        assert result["id"] == 660271

    @patch("external_stats._mlb_request")
    def test_find_mlb_player_fallback_search(self, mock_req):
        """When first endpoint returns None, falls back to people/search."""
        mock_req.side_effect = [
            None,  # first endpoint fails
            {"people": [SAMPLE_MLB_PLAYER]},  # fallback works
        ]
        result = _find_mlb_player("Aaron Judge")
        assert result["id"] == 660271

    @patch("external_stats._mlb_request")
    def test_find_mlb_player_not_found(self, mock_req):
        mock_req.return_value = {"people": []}
        result = _find_mlb_player("Nobody")
        assert result is None

    @patch("external_stats._mlb_request")
    def test_find_mlb_player_both_fail(self, mock_req):
        """Both search endpoints return None."""
        mock_req.side_effect = [None, None]
        result = _find_mlb_player("Nobody")
        assert result is None


# ---------------------------------------------------------------------------
# 5. external_stats — MLB stats & rosters
# ---------------------------------------------------------------------------


class TestMlbStats:

    @patch("external_stats._find_mlb_player")
    @patch("external_stats._mlb_request")
    def test_get_mlb_player_stats_hitting(self, mock_req, mock_find):
        mock_find.return_value = SAMPLE_MLB_PLAYER
        mock_req.return_value = {
            "stats": [{
                "splits": [{
                    "stat": {
                        "gamesPlayed": 150, "avg": ".310", "homeRuns": 52,
                        "rbi": 130, "runs": 110, "stolenBases": 5,
                        "obp": ".420", "slg": ".680", "ops": "1.100",
                        "hits": 175, "atBats": 550, "baseOnBalls": 90,
                        "strikeOuts": 140,
                    }
                }]
            }]
        }
        stats = get_mlb_player_stats("Aaron Judge", stat_group="hitting")

        assert stats["name"] == "Aaron Judge"
        assert stats["hr"] == 52
        assert stats["position"] == "OF"

    @patch("external_stats._find_mlb_player")
    @patch("external_stats._mlb_request")
    def test_get_mlb_player_stats_pitching(self, mock_req, mock_find):
        mock_find.return_value = {
            "id": 100, "fullName": "Gerrit Cole",
            "primaryPosition": {"abbreviation": "SP"},
            "currentTeam": {"name": "New York Yankees"},
        }
        mock_req.return_value = {
            "stats": [{
                "splits": [{
                    "stat": {
                        "gamesPlayed": 30, "wins": 15, "losses": 4,
                        "era": "2.63", "whip": "0.98", "strikeOuts": 220,
                        "inningsPitched": "200.0", "saves": 0, "holds": 0,
                        "baseOnBalls": 40, "hits": 150, "homeRuns": 20,
                    }
                }]
            }]
        }
        stats = get_mlb_player_stats("Gerrit Cole", stat_group="pitching")

        assert stats["wins"] == 15
        assert stats["era"] == "2.63"

    @patch("external_stats._find_mlb_player")
    def test_get_mlb_player_stats_player_not_found(self, mock_find):
        mock_find.return_value = None
        stats = get_mlb_player_stats("Nobody")
        assert stats == {}

    @patch("external_stats._find_mlb_player")
    @patch("external_stats._mlb_request")
    def test_get_mlb_player_stats_no_splits(self, mock_req, mock_find):
        """No splits in the stats response returns empty dict."""
        mock_find.return_value = SAMPLE_MLB_PLAYER
        mock_req.return_value = {"stats": [{"splits": []}]}
        stats = get_mlb_player_stats("Aaron Judge")
        assert stats == {}

    @patch("external_stats._mlb_request")
    def test_get_mlb_team_roster(self, mock_req):
        mock_req.return_value = {
            "roster": [
                {
                    "person": {"id": 660271, "fullName": "Aaron Judge"},
                    "position": {"abbreviation": "OF"},
                    "jerseyNumber": "99",
                    "status": {"description": "Active"},
                }
            ]
        }
        roster = get_mlb_team_roster(147)
        assert len(roster) == 1
        assert roster[0]["name"] == "Aaron Judge"

    @patch("external_stats._mlb_request")
    def test_get_mlb_team_roster_empty(self, mock_req):
        mock_req.return_value = None
        roster = get_mlb_team_roster(147)
        assert roster == []

    @patch("external_stats._mlb_request")
    def test_get_mlb_prospect_rankings(self, mock_req):
        mock_req.return_value = {
            "prospects": [
                {
                    "rank": 1,
                    "person": {
                        "fullName": "Jackson Holliday",
                        "primaryPosition": {"abbreviation": "SS"},
                        "currentAge": 20,
                    },
                    "team": {"name": "Baltimore Orioles"},
                }
            ]
        }
        prospects = get_mlb_prospect_rankings()
        assert len(prospects) == 1
        assert prospects[0]["name"] == "Jackson Holliday"

    @patch("external_stats._mlb_request")
    def test_get_mlb_prospect_rankings_empty(self, mock_req):
        mock_req.return_value = None
        prospects = get_mlb_prospect_rankings()
        assert prospects == []


# ---------------------------------------------------------------------------
# 6. draft_agent — Scoring Models
# ---------------------------------------------------------------------------


class TestScoringModels:

    def test_compute_nba_points_value_typical(self):
        stats = {
            "pts": 25.0, "reb": 7.0, "ast": 8.0,
            "stl": 1.5, "blk": 0.5, "fg3m": 2.0, "tov": 3.0,
        }
        # 25*1 + 7*1.2 + 8*1.5 + 1.5*3 + 0.5*3 + 2*0.5 + 3*(-1)
        # = 25 + 8.4 + 12 + 4.5 + 1.5 + 1.0 - 3.0 = 49.4
        result = compute_nba_points_value(stats)
        assert result == 49.4

    def test_compute_nba_points_value_empty(self):
        assert compute_nba_points_value({}) == 0.0
        assert compute_nba_points_value(None) == 0.0

    def test_compute_mlb_hitting_value_typical(self):
        stats = {
            "runs": 100, "hr": 40, "rbi": 110, "sb": 10,
            "avg": ".300",
        }
        # runs: 100*1 + hr: 40*4 + rbi: 110*1 + sb: 10*2 = 100+160+110+20 = 390
        # avg bonus: (.300 - .270) * 1000 * 5.0 = 30 * 5 = 150
        # total = 540
        result = compute_mlb_hitting_value(stats)
        assert result == 540.0

    def test_compute_mlb_hitting_value_empty(self):
        assert compute_mlb_hitting_value({}) == 0.0
        assert compute_mlb_hitting_value(None) == 0.0

    def test_compute_mlb_pitching_value_typical(self):
        stats = {
            "wins": 15, "so": 200, "saves": 0,
            "era": "3.00", "whip": "1.10",
        }
        # wins: 15*5=75, so: 200*1=200, saves: 0
        # era: 3.00 < 3.50 => no penalty
        # whip: 1.10 < 1.20 => no penalty
        result = compute_mlb_pitching_value(stats)
        assert result == 275.0

    def test_compute_mlb_pitching_value_with_penalties(self):
        stats = {
            "wins": 10, "so": 150, "saves": 0,
            "era": "4.50", "whip": "1.40",
        }
        # wins: 50, so: 150, saves: 0 => base 200
        # era penalty: (4.50-3.50)/0.50 * (-2) = 2 * (-2) = -4
        # whip penalty: (1.40-1.20)/0.10 * (-3) = 2 * (-3) = -6
        result = compute_mlb_pitching_value(stats)
        assert result == 190.0

    def test_compute_mlb_pitching_value_empty(self):
        assert compute_mlb_pitching_value({}) == 0.0
        assert compute_mlb_pitching_value(None) == 0.0

    def test_compute_mlb_pitching_value_bad_era_string(self):
        stats = {"wins": 5, "so": 50, "saves": 0, "era": "N/A", "whip": "bad"}
        # Should handle gracefully; era/whip fall back to 3.50/1.20
        result = compute_mlb_pitching_value(stats)
        assert result == 75.0  # 5*5 + 50*1


# ---------------------------------------------------------------------------
# 7. draft_agent — Trend & Sleeper
# ---------------------------------------------------------------------------


class TestTrendAndSleeper:

    def test_compute_recent_trend_positive(self):
        season = {"pts": 20.0}
        game_log = [{"pts": 25}, {"pts": 27}, {"pts": 24}]
        trend = compute_recent_trend(season, game_log)
        # avg recent = 25.33, trend = (25.33-20)/20*100 = 26.7%
        assert trend > 25

    def test_compute_recent_trend_negative(self):
        season = {"pts": 25.0}
        game_log = [{"pts": 18}, {"pts": 20}, {"pts": 17}]
        trend = compute_recent_trend(season, game_log)
        assert trend < 0

    def test_compute_recent_trend_no_data(self):
        assert compute_recent_trend({}, []) == 0.0
        assert compute_recent_trend({"pts": 20}, []) == 0.0
        assert compute_recent_trend({}, [{"pts": 10}]) == 0.0

    def test_compute_recent_trend_zero_ppg(self):
        """If season pts is 0, avoid division by zero."""
        assert compute_recent_trend({"pts": 0}, [{"pts": 10}]) == 0.0

    def test_identify_sleeper_trending_up(self):
        is_sleeper, reason = identify_sleeper(
            {"pts": 25}, trend=20.0, fantasy_value=30, position="PG"
        )
        assert is_sleeper is True
        assert "+20%" in reason

    def test_identify_sleeper_elite_blocks(self):
        is_sleeper, reason = identify_sleeper(
            {"blk": 2.5}, trend=8.0, fantasy_value=25, position="C"
        )
        assert is_sleeper is True
        assert "blocks" in reason.lower()

    def test_identify_sleeper_elite_steals(self):
        is_sleeper, reason = identify_sleeper(
            {"stl": 2.0}, trend=6.0, fantasy_value=25, position="SG"
        )
        assert is_sleeper is True
        assert "steals" in reason.lower()

    def test_identify_sleeper_spring_training(self):
        stats = {"type": "spring_training", "avg": ".380", "games": 8}
        is_sleeper, reason = identify_sleeper(
            stats, trend=0.0, fantasy_value=10, position="OF"
        )
        assert is_sleeper is True
        assert "spring training" in reason.lower()

    def test_identify_sleeper_not_qualifying(self):
        is_sleeper, reason = identify_sleeper(
            {"pts": 10, "blk": 0.5, "stl": 0.5},
            trend=3.0, fantasy_value=15, position="SF"
        )
        assert is_sleeper is False
        assert reason == ""


# ---------------------------------------------------------------------------
# 8. draft_agent — VOR
# ---------------------------------------------------------------------------


class TestVOR:

    def test_compute_vor_basic(self):
        repl = {"PG": 30.0, "C": 25.0}
        assert compute_vor(45.0, "PG", repl) == 15.0
        assert compute_vor(35.0, "C", repl) == 10.0

    def test_compute_vor_unknown_position(self):
        repl = {"PG": 30.0}
        # Unknown position => replacement = 0
        assert compute_vor(45.0, "UTIL", repl) == 45.0


# ---------------------------------------------------------------------------
# 9. draft_agent — ShippLiveContext
# ---------------------------------------------------------------------------


class TestShippLiveContext:

    def test_connect_success(self):
        ctx = ShippLiveContext(api_key="test-key")
        ctx.session = MagicMock()
        ctx.session.post.return_value = _mock_response(200, {"connection_id": "abc123"})

        assert ctx.connect("nba") is True
        assert ctx.connection_id == "abc123"

    def test_connect_failure(self):
        ctx = ShippLiveContext(api_key="test-key")
        ctx.session = MagicMock()
        ctx.session.post.side_effect = Exception("Connection refused")

        assert ctx.connect("nba") is False
        assert ctx.connection_id is None

    def test_get_live_games_no_connection(self):
        ctx = ShippLiveContext(api_key="test-key")
        assert ctx.get_live_games() == []

    def test_get_live_games_with_events(self):
        ctx = ShippLiveContext(api_key="test-key")
        ctx.connection_id = "abc123"
        ctx.session = MagicMock()
        ctx.session.post.return_value = _mock_response(200, {
            "data": [
                {"id": "evt-98", "status": "live"},
                {"id": "evt-99", "status": "live"},
            ],
        })

        games = ctx.get_live_games()
        assert len(games) == 2
        assert ctx.last_event_id == "evt-99"

    def test_extract_hot_players_live_game(self):
        ctx = ShippLiveContext(api_key="test-key")
        games = [
            {
                "status": "live",
                "home_players": [
                    {"name": "Star Player", "pts": 30, "reb": 12, "ast": 5},
                    {"name": "Bench Guy", "pts": 4, "reb": 2, "ast": 1},
                ],
            }
        ]
        hot = ctx.extract_hot_players(games)
        assert "Star Player" in hot
        assert "Bench Guy" not in hot
        assert hot["Star Player"]["live"] is True

    def test_extract_hot_players_non_live_game(self):
        ctx = ShippLiveContext(api_key="test-key")
        games = [
            {
                "status": "final",
                "home_players": [
                    {"name": "Star Player", "pts": 50, "reb": 15, "ast": 10},
                ],
            }
        ]
        hot = ctx.extract_hot_players(games)
        assert hot == {}

    def test_extract_hot_players_dict_players(self):
        """Players provided as dict instead of list."""
        ctx = ShippLiveContext(api_key="test-key")
        games = [
            {
                "status": "in_progress",
                "players": {
                    "p1": {"player_name": "Hot Shot", "points": 25, "rebounds": 5, "assists": 3},
                },
            }
        ]
        hot = ctx.extract_hot_players(games)
        assert "Hot Shot" in hot

    def test_close(self):
        ctx = ShippLiveContext(api_key="test-key")
        ctx.connection_id = "abc123"
        ctx.session = MagicMock()
        ctx.close()
        ctx.session.post.assert_called_once()


# ---------------------------------------------------------------------------
# 10. draft_agent — _safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:

    def test_valid_float(self):
        assert _safe_float(3.14) == 3.14

    def test_valid_int(self):
        assert _safe_float(5) == 5.0

    def test_string_number(self):
        assert _safe_float("2.5") == 2.5

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_non_numeric_string(self):
        assert _safe_float("abc") == 0.0


# ---------------------------------------------------------------------------
# 11. draft_agent — build_draft_board (integration-level with mocks)
# ---------------------------------------------------------------------------


class TestBuildDraftBoard:

    @patch("draft_agent.get_nba_player_game_log")
    @patch("draft_agent.get_nba_player_season_stats")
    def test_nba_board_filters_drafted(self, mock_stats, mock_log):
        """Drafted players should be excluded from the board."""
        mock_stats.return_value = {
            "player_id": 1, "name": "Test", "team": "TST",
            "position": "PG", "pts": 20, "reb": 5, "ast": 5,
            "stl": 1, "blk": 0.5, "fg3m": 2, "tov": 2,
        }
        mock_log.return_value = []

        ranked = build_draft_board(
            sport="nba",
            scoring_format="points",
            drafted_names=["Nikola Jokic", "Luka Doncic"],
            roster=[],
        )

        names = [rp.name for rp in ranked]
        assert "Nikola Jokic" not in names
        assert "Luka Doncic" not in names

    @patch("draft_agent.get_nba_player_game_log")
    @patch("draft_agent.get_nba_player_season_stats")
    def test_nba_board_sorted_by_vor(self, mock_stats, mock_log):
        """Board should be sorted by VOR descending."""
        call_count = {"n": 0}
        def varying_stats(name, **kw):
            call_count["n"] += 1
            return {
                "player_id": call_count["n"], "name": name, "team": "TST",
                "position": "PG", "pts": 10 + call_count["n"], "reb": 5, "ast": 5,
                "stl": 1, "blk": 0.5, "fg3m": 1, "tov": 2,
            }

        mock_stats.side_effect = varying_stats
        mock_log.return_value = []

        ranked = build_draft_board(
            sport="nba",
            scoring_format="points",
            drafted_names=[],
            roster=[],
        )

        assert len(ranked) > 0
        # VOR should be non-increasing
        for i in range(len(ranked) - 1):
            assert ranked[i].vor >= ranked[i + 1].vor

    def test_unknown_sport_returns_empty(self):
        """Unknown sport produces empty board (all players skipped)."""
        ranked = build_draft_board(
            sport="cricket",
            scoring_format="points",
            drafted_names=[],
            roster=[],
        )
        assert ranked == []


# ---------------------------------------------------------------------------
# 12. draft_agent — format_draft_board
# ---------------------------------------------------------------------------


class TestFormatDraftBoard:

    def test_format_nba_board(self):
        ranked = [
            RankedPlayer(
                name="Test Player", team="TST", position="PG",
                fantasy_value=50.0, vor=15.0,
                season_stats={"pts": 25.0, "reb": 7.0, "ast": 8.0, "stl": 1.5, "blk": 0.5},
                recent_trend=12.0,
                recommendation="BEST AVAILABLE",
            ),
        ]
        output = format_draft_board(ranked, sport="nba", scoring_format="points", top_n=5)

        assert "FANTASY DRAFT ASSISTANT" in output
        assert "NBA" in output
        assert "Test Player" in output
        assert "VOR: +15.0" in output
        assert "BEST AVAILABLE" in output

    def test_format_mlb_hitter(self):
        ranked = [
            RankedPlayer(
                name="Aaron Judge", team="NYY", position="OF",
                fantasy_value=100.0, vor=30.0,
                season_stats={
                    "avg": ".310", "hr": 52, "rbi": 130,
                    "runs": 110, "sb": 5,
                },
            ),
        ]
        output = format_draft_board(ranked, sport="mlb", scoring_format="roto", top_n=5)

        assert "MLB" in output
        assert "Aaron Judge" in output
        assert ".310" in output

    def test_format_sleeper_tag(self):
        ranked = [
            RankedPlayer(
                name="Sleepy Pick", team="TST", position="C",
                fantasy_value=30.0, vor=10.0,
                season_stats={"pts": 15.0, "reb": 10.0, "ast": 3.0, "stl": 0.5, "blk": 2.5},
                is_sleeper=True,
                sleeper_reason="Elite blocks with upward trend",
            ),
        ]
        output = format_draft_board(ranked, sport="nba", scoring_format="points")
        assert "[SLEEPER]" in output
        assert "Elite blocks" in output


# ---------------------------------------------------------------------------
# 13. draft_agent — _count_roster_positions
# ---------------------------------------------------------------------------


class TestCountRosterPositions:

    def test_returns_empty_dict(self):
        """Current implementation returns empty dict for any input."""
        result = _count_roster_positions(["LeBron James"], "nba")
        assert result == {}


# ---------------------------------------------------------------------------
# 14. external_stats — get_nba_all_players
# ---------------------------------------------------------------------------


class TestGetNbaAllPlayers:

    @patch("external_stats._bdl_request")
    def test_pagination(self, mock_req):
        """Should paginate until total_pages reached."""
        mock_req.side_effect = [
            {
                "data": [
                    {"id": 1, "first_name": "Player", "last_name": "One",
                     "position": "PG", "team": {"abbreviation": "TST"}},
                ],
                "meta": {"total_pages": 2},
            },
            {
                "data": [
                    {"id": 2, "first_name": "Player", "last_name": "Two",
                     "position": "SG", "team": {"abbreviation": "TST"}},
                ],
                "meta": {"total_pages": 2},
            },
        ]
        players = get_nba_all_players(season=2025, per_page=1)
        assert len(players) == 2
        assert players[0]["name"] == "Player One"

    @patch("external_stats._bdl_request")
    def test_no_data(self, mock_req):
        mock_req.return_value = None
        players = get_nba_all_players()
        assert players == []


# ---------------------------------------------------------------------------
# 15. external_stats — MLB spring training
# ---------------------------------------------------------------------------


class TestMlbSpringTraining:

    @patch("external_stats._find_mlb_player")
    @patch("external_stats._mlb_request")
    def test_spring_training_hitter(self, mock_req, mock_find):
        mock_find.return_value = {
            "id": 100, "fullName": "Prospect X",
            "primaryPosition": {"abbreviation": "OF"},
            "currentTeam": {"name": "Team A"},
        }
        mock_req.return_value = {
            "stats": [{
                "splits": [{
                    "stat": {
                        "gamesPlayed": 10, "avg": ".380", "homeRuns": 3,
                        "rbi": 8, "runs": 7, "hits": 15, "atBats": 40,
                        "ops": "1.050",
                    }
                }]
            }]
        }
        result = get_mlb_spring_training_stats("Prospect X")
        assert result["type"] == "spring_training"
        assert result["avg"] == ".380"
        assert result["season"] == 2026

    @patch("external_stats._find_mlb_player")
    @patch("external_stats._mlb_request")
    def test_spring_training_pitcher(self, mock_req, mock_find):
        mock_find.return_value = {
            "id": 200, "fullName": "Pitcher Y",
            "primaryPosition": {"abbreviation": "SP"},
            "currentTeam": {"name": "Team B"},
        }
        mock_req.return_value = {
            "stats": [{
                "splits": [{
                    "stat": {
                        "gamesPlayed": 5, "era": "1.50",
                        "inningsPitched": "12.0", "strikeOuts": 15,
                        "baseOnBalls": 3, "whip": "0.85",
                    }
                }]
            }]
        }
        result = get_mlb_spring_training_stats("Pitcher Y")
        assert result["era"] == "1.50"

    @patch("external_stats._find_mlb_player")
    @patch("external_stats._mlb_request")
    def test_spring_training_no_splits(self, mock_req, mock_find):
        """No spring stats yet should return a note."""
        mock_find.return_value = {
            "id": 300, "fullName": "No Stats",
            "primaryPosition": {"abbreviation": "2B"},
            "currentTeam": {"name": "Team C"},
        }
        mock_req.return_value = {
            "stats": [{"splits": []}]
        }
        result = get_mlb_spring_training_stats("No Stats")
        assert "note" in result

    @patch("external_stats._find_mlb_player")
    def test_spring_training_player_not_found(self, mock_find):
        mock_find.return_value = None
        result = get_mlb_spring_training_stats("Nobody")
        assert result == {}

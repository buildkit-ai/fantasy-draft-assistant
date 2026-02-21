# Fantasy Draft Assistant

**Never miss a value pick again.** Real-time player recommendations powered by live game data and season-long stats, right when you need them -- during your fantasy draft.

---

## The Problem

Your fantasy draft is live. The clock is ticking. You need to decide between three players, but you don't know who's been hot lately, who's dealing with a minor tweak, or which position is about to run dry. You're tabbing between five browser windows and still guessing.

## The Solution

Fantasy Draft Assistant merges live game performance with full-season stats to give you a ranked draft board that updates in real time. It knows who's on fire tonight, which positions are getting scarce, and where the value picks are hiding.

```
+--------------------------------------------------+
|  FANTASY DRAFT ASSISTANT — NBA Points League     |
|  Round 7, Pick 3  |  Your roster: 6/13 filled    |
+--------------------------------------------------+
|                                                  |
|  BEST AVAILABLE                                  |
|  ┌──────────────────────────────────────────┐    |
|  │ 1. Darius Garland (PG/SG) — VOR: +14.2  │    |
|  │    Season: 21.3 pts, 8.1 ast, 2.4 3pm   │    |
|  │    Last 10: 24.8 pts (+17% from avg)     │    |
|  │    LIVE: 28 pts at halftime tonight      │    |
|  │    >> RECOMMENDED — fills PG need        │    |
|  ├──────────────────────────────────────────┤    |
|  │ 2. Tyler Herro (SG/SF) — VOR: +11.8     │    |
|  │    Season: 23.7 pts, 5.2 reb, 4.8 ast   │    |
|  │    Last 10: 21.1 pts (-11% from avg)     │    |
|  ├──────────────────────────────────────────┤    |
|  │ 3. Walker Kessler (C) — VOR: +10.4      │    |
|  │    Season: 12.1 pts, 9.8 reb, 2.9 blk   │    |
|  │    Last 10: 14.3 pts (+18% from avg)     │    |
|  │    >> SLEEPER — blocks upside            │    |
|  └──────────────────────────────────────────┘    |
|                                                  |
|  POSITIONAL SCARCITY ALERT                       |
|  PG: 4 quality options left (SCARCE)             |
|  C:  7 quality options left (OK)                 |
|  SF: 11 quality options left (DEEP)              |
+--------------------------------------------------+
```

## Features

- **Live context** — Sees who's performing right now in tonight's games
- **Value over replacement** — Ranks players relative to the next-best option at their position
- **Positional scarcity** — Warns you when a position is drying up
- **Trend detection** — Flags players trending up or down over the last 10 games
- **Sleeper picks** — Surfaces under-drafted players with recent breakout signals
- **Multi-format** — Supports points leagues, category leagues, and roto

## Quick Start

### 1. Install dependencies

```bash
pip install requests
```

### 2. Set your API key

```bash
export SHIPP_API_KEY="your-api-key-here"
```

### 3. Run the draft assistant

```bash
# NBA points league draft
python scripts/draft_agent.py --sport nba --format points

# NBA 9-category league
python scripts/draft_agent.py --sport nba --format categories

# MLB roto league (spring training scouting mode)
python scripts/draft_agent.py --sport mlb --format roto

# Exclude already-drafted players
python scripts/draft_agent.py --sport nba --format points --drafted "LeBron James,Nikola Jokic,Luka Doncic"
```

### 4. During your draft

The assistant outputs a ranked board of available players. After each pick:
- Mark the player as drafted (remove from available pool)
- Add your pick to your roster
- The board re-ranks based on your new positional needs

## How It Works

1. **Live game scan** — Connects to live sports feed for current game data
2. **Season stats pull** — Fetches per-game averages and recent game logs from public stats APIs
3. **Merge and rank** — Combines live signals (hot/cold) with season baselines
4. **VOR calculation** — For each position, computes value above the replacement-level player
5. **Scarcity adjustment** — Positions with fewer quality options get a draft priority boost
6. **Output** — Sorted recommendations with context on why each player ranks where they do

## Data Sources

| Source | Data | Auth Required |
|--------|------|---------------|
| balldontlie.io | NBA season stats, game logs | None |
| statsapi.mlb.com | MLB stats, spring training, rosters | None |

## Configuration

| Environment Variable | Required | Description |
|---------------------|----------|-------------|
| `SHIPP_API_KEY`     | Yes      | API key for live game data |

## License

MIT

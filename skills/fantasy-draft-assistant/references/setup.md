# Setup Guide — fantasy-draft-assistant Skill

This guide walks you through configuring all required and optional API keys
for the `fantasy-draft-assistant` skill.

## Required: Shipp.ai API Key

The Shipp.ai API key is required for live player data, injury feeds, and
real-time game context used to power draft recommendations.

### Steps

1. **Create an account** at [platform.shipp.ai](https://platform.shipp.ai)
2. **Sign in** and navigate to **Settings > API Keys**
3. **Generate a new API key** — copy it immediately (it won't be shown again)
4. **Set the environment variable**:

```bash
# Add to your shell profile (~/.zshrc, ~/.bashrc, etc.)
export SHIPP_API_KEY="shipp_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

5. **Verify** by running:

```bash
curl -s -H "Authorization: Bearer $SHIPP_API_KEY" \
  "https://api.shipp.ai/api/v1/connections" | python3 -m json.tool
```

You should see a JSON response (even if the connections list is empty).

### API Key Format

Shipp API keys typically start with `shipp_live_` or `shipp_test_`. Use the
`live` key for production sports data.

### Rate Limits

Your rate limit depends on your Shipp.ai plan:

| Plan       | Requests/min | Connections | Notes                    |
|------------|-------------|-------------|--------------------------|
| Free       | 30          | 3           | Great for trying it out  |
| Starter    | 120         | 10          | Suitable for one sport   |
| Pro        | 600         | 50          | All three sports         |
| Enterprise | Custom      | Unlimited   | Contact sales            |

## No Key Required

The following external sources do not require API keys:

- **balldontlie API** — NBA player stats and season averages
  - Base URL: `https://api.balldontlie.io/v1`
  - Rate limit: ~30 requests/minute
  - Data: Player search, season averages, career stats

- **MLB Stats API** — MLB rosters, player stats, schedules
  - Base URL: `https://statsapi.mlb.com/api/v1`
  - Rate limit: No strict limit (be courteous, ~1 req/sec)
  - Data: Rosters, player stats, team info, game scores

- **FanGraphs** — Player projections and advanced metrics
  - Base URL: `https://www.fangraphs.com`
  - Rate limit: Be courteous (~1 req/sec)
  - Data: Steamer/ZiPS projections, WAR, advanced batting and pitching stats

## Python Dependencies

Install the required package:

```bash
pip install requests
```

All other dependencies are from the Python standard library (`os`, `time`,
`logging`, `datetime`, `json`, `typing`).

## Environment Variable Summary

| Variable        | Required | Source             | Purpose                            |
|-----------------|----------|--------------------|-------------------------------------|
| `SHIPP_API_KEY` | Yes      | platform.shipp.ai  | Live player data, injuries, context |

## Verifying Your Setup

Run the built-in smoke test:

```bash
cd skills/community/fantasy-draft-assistant
python3 scripts/draft_agent.py --sport nba --once
```

This will attempt to:
1. Fetch live NBA player data (requires `SHIPP_API_KEY`)
2. Fetch NBA player stats via balldontlie (no key needed)
3. Pull projection data from FanGraphs (no key needed)
4. Generate a sample draft recommendation

Each section will show either data or an error message indicating which
key is missing or which service is unavailable.

## Troubleshooting

### "SHIPP_API_KEY environment variable is not set"

Your shell session doesn't have the key. Make sure you either:
- Added `export SHIPP_API_KEY=...` to your shell profile and restarted the terminal
- Or ran the export command in the current session

### "Shipp API 401: Unauthorized"

The key is set but invalid. Double-check:
- No extra spaces or newline characters in the key
- The key is from the correct environment (live vs test)
- The key hasn't been revoked

### "Shipp API 402: Payment Required"

Your plan's quota has been exceeded. Check your usage at
[platform.shipp.ai/usage](https://platform.shipp.ai) or upgrade your plan.

### "Shipp API 429: Too Many Requests"

You've hit the rate limit. The draft assistant automatically retries with
backoff, but if it persists, reduce polling frequency or upgrade your plan.

### balldontlie or MLB Stats API returning errors

These free APIs occasionally experience downtime. The assistant will fall
back to cached data when available and retry automatically.

### FanGraphs projections unavailable

FanGraphs may occasionally be unreachable or block automated requests. The
assistant will use the most recent cached projections when this occurs.

## Documentation Links

- **Shipp.ai Docs**: [docs.shipp.ai](https://docs.shipp.ai)
- **Shipp.ai API Reference**: [docs.shipp.ai/api](https://docs.shipp.ai/api)
- **balldontlie Docs**: [balldontlie.io](https://www.balldontlie.io)
- **MLB Stats API**: Community docs at [github.com/toddrob99/MLB-StatsAPI](https://github.com/toddrob99/MLB-StatsAPI)
- **FanGraphs**: [fangraphs.com](https://www.fangraphs.com)

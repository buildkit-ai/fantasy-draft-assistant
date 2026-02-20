# GUI Specification: fantasy-draft-assistant

## Design Direction
- **Style:** Dark theme with a structured draft board grid. Split-panel interface optimized for rapid decision-making during live drafts. Data-forward with clear visual hierarchy separating available players from AI recommendations.
- **Inspiration:** Yahoo Fantasy draft room interface, Sleeper app draft board dark mode on Dribbble, Wall Street trading desk order-entry panels on Behance
- **Mood:** Strategic, urgent, analytical, decisive

## Layout
- **Primary view:** Split panel. Left panel (60%) contains a scrollable, filterable player list table with sortable columns (rank, name, position, team, projected points, ADP, value score). Right panel (40%) shows the AI recommendation card stack with top 3 suggested picks, rationale text, and one-click draft action. Top strip (64px) contains the draft pick tracker showing round/pick number and a horizontal timeline of completed picks.
- **Mobile:** Switches to tabbed view. Tab 1: player list (full width). Tab 2: AI recommendations (full width). Draft pick tracker remains sticky at top in compact single-row mode.
- **Header:** Draft pick tracker strip with: current round/pick badge (left), horizontal scrolling pick timeline with team logos (center), timer countdown and "On the Clock" indicator (right).

## Color Palette
- Background: #0F172A (Deep Navy)
- Surface: #1E293B (Slate Panel)
- Primary accent: #8B5CF6 (Vivid Purple) — AI-recommended picks, highlighted rows, action buttons
- Success: #34D399 (Value Green) — value picks, positive projections, upside indicators
- Warning: #F59E0B (Draft Amber) — on-the-clock timer, caution flags on injury-prone players
- Text primary: #F1F5F9 (Snow White)
- Text secondary: #94A3B8 (Cool Slate)

## Component Structure
- **DraftBoard** — Horizontal scrolling grid showing all draft rounds and picks. Completed picks show team logo + player name. Current pick pulses with purple border. Future picks are dimmed.
- **PlayerListTable** — Sortable, filterable data table with columns for rank, player name, position badge, team, projected points, ADP, and value score. Row click expands inline detail. Supports search and position filter tabs (ALL/QB/RB/WR/TE/K/DEF).
- **RecommendationCard** — Stacked card (1 of 3) showing recommended player with: name, position, team, headshot placeholder, projected points, value rationale (2-3 sentences from AI), and a prominent "DRAFT" action button.
- **PickTimer** — Countdown timer with circular progress ring. Turns amber under 30s, red under 10s. Shows "YOUR PICK" or opponent team name.
- **PositionFilter** — Horizontal pill-button row for filtering the player list by position. Active position pill uses purple accent.
- **ValueBadge** — Small badge on player rows indicating value tier: "STEAL" (green), "FAIR" (grey), "REACH" (amber). Calculated from ADP vs current pick number.
- **DraftLog** — Collapsible bottom panel showing chronological log of all picks made, with pick number, team, player, and position.

## Typography
- Headings: Inter Bold, 18-24px, letter-spacing -0.01em, #F1F5F9
- Body: Inter Regular, 13-15px, line-height 1.5, #94A3B8 for secondary, #F1F5F9 for primary
- Stats/numbers: JetBrains Mono Medium, 14-20px for projections and ADP values, tabular-nums enabled

## Key Interactions
- **Draft action:** Clicking "DRAFT" on a recommendation card triggers a confirmation modal (250ms slide-up), then animates the player into the draft board slot with a purple glow trail.
- **Player search:** Typing in the search field instantly filters the player list with debounced input (150ms). Matching text is highlighted in purple.
- **Sort toggle:** Clicking a column header sorts ascending; clicking again sorts descending. Active sort column header is purple-underlined with a directional arrow.
- **Recommendation refresh:** When a pick is made (by user or opponent), the recommendation cards re-evaluate and cross-fade to updated suggestions within 400ms.
- **On-the-clock alert:** When it becomes the user's turn, the header flashes purple twice, the timer starts, and a subtle audio ping plays (if enabled).
- **Row expansion:** Clicking a player row in the table expands an inline detail section showing season projections, recent performance, bye week, and injury status.

## Reference Screenshots
- [Yahoo Fantasy Draft Room Dark on Dribbble](https://dribbble.com/search/fantasy-draft-board) — Grid-based draft board with real-time pick tracking and dark theme
- [Sleeper App Draft Interface on Behance](https://www.behance.net/search/projects?search=fantasy+draft+app) — Mobile-first draft interface with AI recommendation panels and position filtering
- [Trading Desk Order Entry on Mobbin](https://mobbin.com/search/trading-order-entry) — Split-panel decision interface with urgency timers and action buttons

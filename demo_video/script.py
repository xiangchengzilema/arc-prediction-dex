"""Pythia demo video — narration script + slide spec.

New 9-slide structure (2026-05-18, post-honesty pivot):
  1. Cover (static)
  2. Demo: home + markets (recording)
  3. Demo: market detail + price chart + orderbook (recording)
  4. Demo: live trade (recording)
  5. Demo: Pythia agent decision log (recording)
  6. Demo: auto-keeper lifecycle (recording)
  7. Architecture: 3 repos (static)
  8. What's real / What's simulated (static, honest)
  9. Roadmap to Arc on-chain (static)
"""
SLIDES = [
    # ──────────────── 1 ── COVER ────────────────
    {
        "id": 1,
        "kind": "cover",
        "title": "Pythia",
        "eyebrow": "An AI agent for prediction markets, built for Arc",
        "stats": [
            ("4", "public repos"),
            ("~6,000", "lines of Python"),
            ("89", "passing tests"),
            ("Live", "demo running"),
        ],
        "footer": "github.com/xiangchengzilema  ·  Submitted to Agora Hackathon",
        "voice": (
            "Pythia. An A-I agent that watches prediction markets, sizes bets with the Kelly criterion, "
            "and is built to settle in U-S-D-C on Arc. Submitted to the Agora hackathon. "
            "Let me show you what works today."
        ),
        "seconds": 13,
    },

    # ──────────────── 2 ── DEMO: HOME ────────────────
    {
        "id": 2,
        "kind": "demo",
        "title": "The home page",
        "eyebrow": "What you see when you open the demo",
        "screen_clip": "home_scroll",
        "right_text": [
            "Live ticker · 5 active markets",
            "Probability rings, sparklines, deadline countdown",
            "Filter tabs and search",
            "Pythia P&L card auto-refreshing every 15s",
        ],
        "footer": "web-production-b5036.up.railway.app",
        "voice": (
            "The home page. A live ticker, five prediction markets each with a probability ring, "
            "a 24-hour sparkline, and a deadline countdown. The Pythia P-and-L card up top auto-refreshes "
            "every fifteen seconds."
        ),
        "seconds": 18,
    },

    # ──────────────── 3 ── DEMO: MARKET DETAIL ────────────────
    {
        "id": 3,
        "kind": "demo",
        "title": "Market detail",
        "eyebrow": "Click any market to drill in",
        "screen_clip": "market_detail",
        "right_text": [
            "4-step lifecycle: Open → Closed → Resolved → Settled",
            "Pythia view card · agent's verdict on this market",
            "Live YES price chart, recent trades, depth book",
            "Sticky trade panel · Buy / Sell / Limit",
        ],
        "footer": "Polymarket-quality UI, 740 lines of HTML in market_detail.html",
        "voice": (
            "The market detail page. A four-step lifecycle indicator at the top — open, closed, resolved, settled. "
            "A Pythia view card that explains what the agent thinks of this market and why. "
            "A live price chart, the order book, and a sticky trade panel on the right."
        ),
        "seconds": 22,
    },

    # ──────────────── 4 ── DEMO: TRADE ────────────────
    {
        "id": 4,
        "kind": "demo",
        "title": "Place a trade",
        "eyebrow": "Buy YES on the AMM with one click",
        "screen_clip": "trade",
        "right_text": [
            "Quote refreshes as you type the amount",
            "Slippage estimate · auto-blocks > 30%",
            "Buy executes against AMM, returns shares + avg price",
            "Position appears in /portfolio in real time",
        ],
        "footer": "amm.py  ·  Maniswap-style two-step swap, YES + NO = $1.00",
        "voice": (
            "Placing a trade. The quote updates as you type. Slippage is estimated up front, and the "
            "buy button locks if slippage exceeds thirty percent. After confirming, the position shows "
            "up in your portfolio immediately."
        ),
        "seconds": 22,
    },

    # ──────────────── 5 ── DEMO: AGENT ────────────────
    {
        "id": 5,
        "kind": "demo",
        "title": "Pythia in the loop",
        "eyebrow": "/agent · the AI control room",
        "screen_clip": "agent",
        "right_text": [
            "30-second decision cycle, scanning every market",
            "Belief vs market: target | current | edge",
            "Decision log — every action with full reasoning",
            "Quarter-Kelly sizing · 3% minimum edge filter",
        ],
        "footer": "agent.py · ring buffer of 50 most recent decisions",
        "voice": (
            "This is where Pythia thinks. Every thirty seconds the agent scans every market. "
            "On the left, target probability versus market price and the resulting edge. "
            "On the right, the decision log — every buy, hold, and skip with full reasoning."
        ),
        "seconds": 22,
    },

    # ──────────────── 6 ── DEMO: KEEPER ────────────────
    {
        "id": 6,
        "kind": "demo",
        "title": "The venue keeps itself alive",
        "eyebrow": "Background keeper · runs every 60 seconds",
        "screen_clip": "keeper",
        "right_text": [
            "Closes markets when their deadline passes",
            "Auto-resolves CLOSED markets after dispute window",
            "Replenishes from a 20-entry pool when OPEN < 4",
            "Server rejects buys on closed or expired markets",
        ],
        "footer": "keeper.py · /api/keeper/status exposes recent close/mint events",
        "voice": (
            "And the venue self-maintains. A background thread sweeps every minute. It closes markets "
            "when the deadline hits, auto-resolves them after the dispute window using A-M-M prices, and "
            "mints fresh markets when the active count drops. One operator, perpetually live exchange."
        ),
        "seconds": 18,
    },

    # ──────────────── 7 ── ARCHITECTURE ────────────────
    {
        "id": 7,
        "kind": "three_cols",
        "title": "Three repos, one loop",
        "eyebrow": "Each layer is a complete codebase",
        "cols": [
            {
                "label": "1 — BRAIN",
                "name": "arc-predict-agent",
                "lines": [
                    "Signal aggregation",
                    "Probability estimation",
                    "Quarter-Kelly sizing",
                    "Backtesting",
                ],
            },
            {
                "label": "2 — VENUE",
                "name": "arc-prediction-dex",
                "lines": [
                    "Maniswap binary AMM",
                    "CLOB order book",
                    "UMA optimistic oracle",
                    "Lifecycle keeper",
                ],
            },
            {
                "label": "3 — SETTLEMENT",
                "name": "arc-micropayments",
                "lines": [
                    "Circle USDC rails",
                    "Circle Nanopayments",
                    "Programmatic wallets",
                    "Webhook subscriptions",
                ],
            },
        ],
        "footer": "Three repositories. One loop: signal → size → execute → settle.",
        "voice": (
            "Three repositories. Brain estimates probabilities and sizes positions. Venue runs the exchange. "
            "Settlement integrates Circle U-S-D-C and Nanopayments. One loop: signal, size, execute, settle."
        ),
        "seconds": 16,
    },

    # ──────────────── 8 ── WHAT'S REAL / WHAT'S SIMULATED ────────────────
    {
        "id": 8,
        "kind": "honesty",
        "title": "What's real, what's not",
        "eyebrow": "Honest framing for the judges",
        "real": [
            "The full U-I (8 pages, Polymarket-style)",
            "AMM, order book, oracle, keeper logic",
            "Pythia agent loop and decision log",
            "89 passing unit tests, 4 public repos",
        ],
        "simulated": [
            "Trades execute against SQLite, not Arc contracts",
            "Pythia P&L is the agent trading against itself",
            "No real users, no real USDC yet",
            "Not yet deployed on Arc testnet",
        ],
        "footer": "I built a working prototype, not a finished product. Here's the difference.",
        "voice": (
            "Be straight with you. The U-I, the matching logic, the agent, the keeper — those are real "
            "and tested. What's simulated: trades clear in SQL-ite, not on Arc smart contracts. The "
            "P-and-L is the agent trading against itself. There are no real users yet. "
            "I wanted you to know what you are looking at."
        ),
        "seconds": 24,
    },

    # ──────────────── 9 ── ROADMAP ────────────────
    {
        "id": 9,
        "kind": "roadmap",
        "title": "From simulator to Arc DEX",
        "eyebrow": "Post-hackathon roadmap · 6-9 weeks",
        "phases": [
            ("PHASE 1", "Solidity contracts", "AMM, orderbook, oracle, factory · 2-3 wks"),
            ("PHASE 2", "Deploy on Arc Testnet", "All 4 contracts verified · 3-5 days"),
            ("PHASE 3", "Wallet + on-chain UI", "MetaMask + Circle Wallet · 1 wk"),
            ("PHASE 4", "Pythia trades on-chain", "web3.py replaces SQL writes · 3-5 days"),
            ("PHASE 5", "Real users on testnet", "Invite 5-10 traders, record real volume · 1-2 wks"),
        ],
        "footer": "github.com/xiangchengzilema  ·  zsc552469237@gmail.com  ·  Thank you.",
        "voice": (
            "Here's the roadmap. Six to nine weeks to migrate from a simulator to a real on-chain "
            "exchange on Arc. Solidity contracts, testnet deployment, wallet integration, and finally real "
            "users. Source on GitHub, thanks for watching."
        ),
        "seconds": 16,
    },
]

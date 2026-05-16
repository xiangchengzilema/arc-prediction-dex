"""Pythia AI Agent - autonomous prediction-market trader.

Pythia continuously monitors live AMM prices, compares them against its
internal probability model, and opens / sizes positions using a simplified
Kelly fraction. Every decision is logged with a human-readable rationale so
the dashboard can stream the agent's reasoning in real time.

The model used here is intentionally lightweight (deterministic seeded
"prior" per market). The point of this module is to demonstrate the agent
loop, decision logging, and Arc-native execution path - not to be alpha.
"""
from __future__ import annotations

import hashlib
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional


@dataclass
class AgentDecision:
    """One decision log entry for the dashboard."""
    timestamp: str
    market_id: str
    market_question: str
    action: str  # "BUY", "HOLD", "SKIP"
    outcome: Optional[str]
    amount_usdc: Optional[float]
    current_price: float
    target_prob: float
    edge: float  # signed: positive = price below target = buy YES
    rationale: str
    pnl_estimate: Optional[float] = None


class PythiaAgent:
    """Stateful background trading agent.

    The agent is single-instance per process. It owns a rolling decision log
    (capped) that the Flask dashboard reads to render activity.
    """

    LOG_CAPACITY = 50
    MIN_EDGE = 0.03         # only act when |price - target| > 3%
    KELLY_FRACTION = 0.25   # quarter-Kelly for safety
    MAX_TRADE_USDC = 25.0   # demo cap per trade
    BANKROLL_USDC = 500.0   # imaginary bankroll for sizing

    def __init__(self, market_engine, get_amm_pool, db_path: str,
                 portfolio=None, analytics=None):
        self.market_engine = market_engine
        self.get_amm = get_amm_pool
        self.db_path = db_path
        self.portfolio = portfolio
        self.analytics = analytics
        self.decisions: Deque[AgentDecision] = deque(maxlen=self.LOG_CAPACITY)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.user_id = "pythia_agent"
        self.cycle_seconds = 30
        self.last_cycle_at: Optional[str] = None

    def _target_probability(self, market_id: str) -> float:
        """Deterministic per-market 'belief'.

        Uses the market id as seed so the agent has a stable view across
        cycles. Range tilted toward 0.4-0.7 so the agent has interesting
        (but not extreme) edges to act on.
        """
        h = hashlib.sha256(market_id.encode()).digest()
        seed = int.from_bytes(h[:8], "big")
        rng = random.Random(seed)
        return round(0.40 + rng.random() * 0.30, 4)

    def _kelly_size(self, edge: float, price: float) -> float:
        """Kelly fraction scaled by bankroll, then capped.

        Kelly for a binary outcome at price p with edge e is roughly
        f* = e / (1 - p) when betting the under-priced side. We use a
        quarter-Kelly to keep position sizes sensible in demo land.
        """
        if price <= 0 or price >= 1:
            return 0.0
        kelly = max(0.0, edge / (1.0 - price))
        size = self.BANKROLL_USDC * self.KELLY_FRACTION * kelly
        return round(min(size, self.MAX_TRADE_USDC), 2)

    def _record(self, d: AgentDecision) -> None:
        with self._lock:
            self.decisions.append(d)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _evaluate_market(self, market: Dict) -> AgentDecision:
        market_id = market["market_id"]
        question = market.get("question", "")[:80]
        target = self._target_probability(market_id)

        try:
            pool = self.get_amm(market_id)
            yes_price = pool.get_price("YES")
        except Exception as e:
            return AgentDecision(
                timestamp=self._now_iso(),
                market_id=market_id,
                market_question=question,
                action="SKIP",
                outcome=None,
                amount_usdc=None,
                current_price=0.5,
                target_prob=target,
                edge=0.0,
                rationale=f"AMM unavailable: {e}",
            )

        edge = target - yes_price  # positive => YES under-priced => buy YES
        if abs(edge) < self.MIN_EDGE:
            return AgentDecision(
                timestamp=self._now_iso(),
                market_id=market_id,
                market_question=question,
                action="HOLD",
                outcome=None,
                amount_usdc=None,
                current_price=yes_price,
                target_prob=target,
                edge=edge,
                rationale=(
                    f"Price {yes_price:.3f} within {self.MIN_EDGE:.0%} of "
                    f"target {target:.3f} - no edge."
                ),
            )

        # Decide side
        if edge > 0:
            outcome = "YES"
            ref_price = yes_price
        else:
            outcome = "NO"
            ref_price = 1.0 - yes_price

        amount = self._kelly_size(abs(edge), ref_price)
        if amount < 1.0:
            return AgentDecision(
                timestamp=self._now_iso(),
                market_id=market_id,
                market_question=question,
                action="HOLD",
                outcome=outcome,
                amount_usdc=amount,
                current_price=yes_price,
                target_prob=target,
                edge=edge,
                rationale=(
                    f"Edge {edge:+.3f} but Kelly size ${amount:.2f} below "
                    f"min trade threshold."
                ),
            )

        # Execute
        try:
            result = pool.buy_outcome(
                outcome, amount, max_slippage=0.20, user_id=self.user_id
            )
            shares = result.get("shares_received", 0.0)
            avg_price = result.get("avg_price", ref_price)
            # Record into portfolio + analytics so dashboard P&L reflects
            # the agent's positions.
            if self.portfolio is not None:
                try:
                    self.portfolio.open_position(
                        self.user_id, market_id, outcome, shares, avg_price,
                        fee=result.get("fee_charged", 0),
                    )
                except Exception:
                    pass
            if self.analytics is not None:
                try:
                    self.analytics.record_volume(market_id, amount, "BUY",
                                                 self.user_id)
                except Exception:
                    pass
            try:
                self.market_engine.update_volume(market_id, amount, self.user_id)
            except Exception:
                pass
            pnl = (target - avg_price) * shares if outcome == "YES" \
                else ((1.0 - target) - avg_price) * shares
            rationale = (
                f"Edge {edge:+.3f}: target {target:.2f} vs price "
                f"{yes_price:.2f}. Buy {outcome} via Kelly sizing."
            )
            return AgentDecision(
                timestamp=self._now_iso(),
                market_id=market_id,
                market_question=question,
                action="BUY",
                outcome=outcome,
                amount_usdc=amount,
                current_price=yes_price,
                target_prob=target,
                edge=edge,
                rationale=rationale,
                pnl_estimate=round(pnl, 2),
            )
        except ValueError as e:
            return AgentDecision(
                timestamp=self._now_iso(),
                market_id=market_id,
                market_question=question,
                action="SKIP",
                outcome=outcome,
                amount_usdc=amount,
                current_price=yes_price,
                target_prob=target,
                edge=edge,
                rationale=f"Order rejected: {e}",
            )

    def _cycle(self) -> None:
        """Run one decision pass over all open markets."""
        try:
            data = self.market_engine.list_markets(status="OPEN", limit=50)
            markets = data.get("markets", [])
        except Exception as e:
            self._record(AgentDecision(
                timestamp=self._now_iso(),
                market_id="-",
                market_question="-",
                action="SKIP",
                outcome=None,
                amount_usdc=None,
                current_price=0.0,
                target_prob=0.0,
                edge=0.0,
                rationale=f"market list failed: {e}",
            ))
            return
        for m in markets:
            decision = self._evaluate_market(m)
            self._record(decision)
        self.last_cycle_at = self._now_iso()

    def _run(self) -> None:
        # Stagger first run a bit so the web server is ready
        time.sleep(5)
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:
                self._record(AgentDecision(
                    timestamp=self._now_iso(),
                    market_id="-",
                    market_question="-",
                    action="SKIP",
                    outcome=None,
                    amount_usdc=None,
                    current_price=0.0,
                    target_prob=0.0,
                    edge=0.0,
                    rationale=f"cycle error: {e}",
                ))
            self._stop.wait(self.cycle_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="pythia-agent", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self, limit: int = 30) -> Dict:
        with self._lock:
            items = list(self.decisions)[-limit:]
        items.reverse()  # newest first
        buys = sum(1 for d in items if d.action == "BUY")
        holds = sum(1 for d in items if d.action == "HOLD")
        return {
            "agent": "Pythia",
            "status": "running" if self._thread and self._thread.is_alive() else "stopped",
            "cycle_seconds": self.cycle_seconds,
            "last_cycle_at": self.last_cycle_at,
            "decisions_total": len(self.decisions),
            "recent_buys": buys,
            "recent_holds": holds,
            "decisions": [asdict(d) for d in items],
        }

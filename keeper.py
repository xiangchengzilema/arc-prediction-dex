"""Market keeper — background thread that runs the lifecycle.

Three jobs run every 60 seconds:
1. sweep_expired_markets() : OPEN markets past deadline -> CLOSED
2. auto_resolve_closed()   : CLOSED markets past dispute_deadline -> RESOLVED
3. replenish_markets()     : if OPEN count < TARGET, mint fresh markets
"""
import os
import time
import threading
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from market_engine import MarketEngine
from amm import AMMPool
from liquidity_pool import LiquidityPoolManager
from oracle import ResolutionOracle

log = logging.getLogger("keeper")

TARGET_OPEN_MARKETS = 4
SWEEP_INTERVAL_SEC = 60

# Pool of fresh markets to inject when count drops. Uses relative deadlines so
# they always feel current. Each entry: (question, category, days, liquidity).
MARKET_POOL = [
    ("Will BTC reach a new ATH in the next 30 days?", "crypto", 30, 8000),
    ("Will ETH break $5,000 in the next 60 days?", "crypto", 60, 6000),
    ("Will the Fed cut rates at the next FOMC meeting?", "economics", 45, 5000),
    ("Will SOL outperform ETH over the next 30 days?", "crypto", 30, 5000),
    ("Will NVDA hit a new all-time high this quarter?", "stocks", 90, 4000),
    ("Will Anthropic release a new flagship model in 60 days?", "AI", 60, 5000),
    ("Will Apple announce a new AI feature at the next event?", "tech", 45, 4000),
    ("Will US CPI come in below consensus next month?", "economics", 30, 5000),
    ("Will Coinbase stock close above $300 in 30 days?", "stocks", 30, 4000),
    ("Will Polymarket volume cross $5B this year?", "crypto", 90, 6000),
    ("Will Tesla deliveries beat estimates next quarter?", "stocks", 90, 4000),
    ("Will OpenAI release a new model in the next 60 days?", "AI", 60, 6000),
    ("Will gold break $3,000/oz in the next 90 days?", "commodities", 90, 5000),
    ("Will USDC supply grow more than 5% in 30 days?", "stablecoin", 30, 7000),
    ("Will the next Fed minutes use the word \"persistent\"?", "economics", 21, 4000),
    ("Will Ripple win its appeal in the next 60 days?", "legal", 60, 4000),
    ("Will Arc mainnet launch by end of next quarter?", "crypto", 90, 8000),
    ("Will Bitcoin dominance exceed 60% in 30 days?", "crypto", 30, 5000),
    ("Will any L2 hit $50B TVL in 90 days?", "crypto", 90, 5000),
    ("Will OpenAI's revenue cross $5B run-rate this year?", "AI", 60, 5000),
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Handle both "Z" and "+00:00" suffixes
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class MarketKeeper:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("PREDICT_DEX_DB", "prediction_dex.db")
        self.engine = MarketEngine(db_path=self.db_path)
        self.liquidity_mgr = LiquidityPoolManager(db_path=self.db_path)
        self.oracle = ResolutionOracle(db_path=self.db_path)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_sweep_at: Optional[str] = None
        self.last_replenish_at: Optional[str] = None
        self.events: list = []  # ring buffer of recent keeper actions

    def _log_event(self, kind: str, msg: str):
        ts = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.events.append({"ts": ts, "kind": kind, "msg": msg})
        if len(self.events) > 50:
            self.events = self.events[-50:]
        log.info("[keeper:%s] %s", kind, msg)

    # ─── Job 1: sweep expired markets ─────────────────────────────────
    def sweep_expired_markets(self) -> int:
        now = _now_utc()
        try:
            data = self.engine.list_markets(status="OPEN", limit=100)
        except Exception as e:
            self._log_event("error", f"list OPEN failed: {e}")
            return 0
        closed = 0
        for m in data.get("markets", []):
            dl = _parse_iso(m.get("deadline"))
            if dl is None or dl > now:
                continue
            try:
                self.engine.close_market(m["market_id"], reason="deadline reached")
                closed += 1
                self._log_event("close", f"{m['market_id'][:14]}... · {m.get('question','')[:50]}")
            except Exception as e:
                self._log_event("error", f"close {m['market_id']} failed: {e}")
        return closed

    # ─── Job 2: auto-resolve CLOSED markets after dispute window ──────
    def auto_resolve_closed(self) -> int:
        now = _now_utc()
        try:
            data = self.engine.list_markets(status="CLOSED", limit=100)
        except Exception as e:
            self._log_event("error", f"list CLOSED failed: {e}")
            return 0
        resolved = 0
        for m in data.get("markets", []):
            dd = _parse_iso(m.get("dispute_deadline"))
            if dd is None or dd > now:
                continue
            # Pick winner from final AMM YES price (>0.5 = YES wins).
            try:
                pool = AMMPool(m["market_id"], db_path=self.db_path)
                yp = pool.get_price("YES")
                winner = "YES" if yp >= 0.5 else "NO"
                self.engine.resolve_market(m["market_id"], winning_outcome=winner)
                resolved += 1
                self._log_event("resolve",
                                f"{m['market_id'][:14]}... resolved {winner} (final ${yp:.2f})")
            except Exception as e:
                self._log_event("error", f"resolve {m['market_id']} failed: {e}")
        return resolved

    # ─── Job 3: keep at least TARGET_OPEN_MARKETS open ────────────────
    def replenish_markets(self) -> int:
        try:
            data = self.engine.list_markets(status="OPEN", limit=100)
            open_count = len(data.get("markets", []))
        except Exception as e:
            self._log_event("error", f"list OPEN for replenish failed: {e}")
            return 0
        if open_count >= TARGET_OPEN_MARKETS:
            return 0

        # Find which pool entries are unused (by question text).
        try:
            all_data = self.engine.list_markets(limit=500)
            used_questions = {m.get("question", "") for m in all_data.get("markets", [])}
        except Exception:
            used_questions = set()
        candidates = [t for t in MARKET_POOL if t[0] not in used_questions]
        random.shuffle(candidates)

        need = TARGET_OPEN_MARKETS - open_count
        created = 0
        now = _now_utc()
        for question, category, days, liquidity in candidates:
            if created >= need:
                break
            deadline = (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                result = self.engine.create_market(
                    question=question,
                    outcomes=["YES", "NO"],
                    deadline=deadline,
                    creator_id="keeper",
                    category=category,
                )
                mid = result["market_id"]
                pool = AMMPool(mid, db_path=self.db_path)
                pool.initialize(liquidity, creator_id="keeper")
                try:
                    self.liquidity_mgr.create_pool(mid, liquidity, "keeper")
                except Exception:
                    pass
                self.engine.open_market(mid, initial_liquidity=liquidity)
                created += 1
                self._log_event("mint", f"{mid[:14]}... · {question[:60]}")
            except Exception as e:
                self._log_event("error", f"mint failed: {e}")
        return created

    # ─── Main loop ────────────────────────────────────────────────────
    def _cycle(self):
        try:
            n_closed = self.sweep_expired_markets()
            n_resolved = self.auto_resolve_closed()
            n_minted = self.replenish_markets()
            self.last_sweep_at = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
            if n_closed or n_resolved or n_minted:
                self.last_replenish_at = self.last_sweep_at
        except Exception as e:
            self._log_event("error", f"cycle error: {e}")

    def _run(self):
        time.sleep(7)  # let app finish booting + seed_demo
        while not self._stop.is_set():
            self._cycle()
            self._stop.wait(SWEEP_INTERVAL_SEC)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="market-keeper", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def snapshot(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_sweep_at": self.last_sweep_at,
            "last_replenish_at": self.last_replenish_at,
            "target_open_markets": TARGET_OPEN_MARKETS,
            "interval_seconds": SWEEP_INTERVAL_SEC,
            "recent_events": list(reversed(self.events[-15:])),
        }

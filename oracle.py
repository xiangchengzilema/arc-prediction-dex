#!/usr/bin/env python3
"""Multi-Source Resolution Oracle for Prediction Markets.

Implements a decentralized oracle system for market resolution with:
- Multiple data source aggregation
- Proposal and dispute mechanism (UMA-style)
- Bonded voting for resolution disputes
- Confidence scoring for automated resolution
- Historical resolution tracking

Inspired by UMA (Universal Market Access) optimistic oracle pattern.

Usage:
    oracle = ResolutionOracle(db_path=":memory:")
    oracle.register_source("coin_gecko", "crypto_price", confidence=0.9)
    proposal = oracle.propose_resolution("btc_100k", "YES", "alice",
                                          evidence="BTC reached $151k on CoinGecko")
    # After dispute period...
    result = oracle.finalize_resolution("btc_100k")
"""
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


class ResolutionOracle:
    """Multi-source oracle for prediction market resolution."""

    DISPUTE_BOND_USDC = 100.0  # Minimum bond to dispute
    DISPUTE_PERIOD_HOURS = 24
    RESOLUTION_THRESHOLD = 0.7  # Confidence threshold for auto-resolve

    def __init__(self, db_path: str = "oracle.db"):
        self.db_path = db_path
        self._conn = None
        self._init_db()

    def _get_conn(self):
        """Get cached database connection."""
        if not hasattr(self, '_conn') or self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = None
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oracle_sources (
                source_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                base_url TEXT,
                api_key_env TEXT,
                confidence REAL NOT NULL DEFAULT 0.5,
                reliability_score REAL NOT NULL DEFAULT 0.5,
                total_queries INTEGER NOT NULL DEFAULT 0,
                successful_queries INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resolution_proposals (
                proposal_id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                proposed_outcome TEXT NOT NULL,
                proposer_id TEXT NOT NULL,
                evidence TEXT,
                bond_amount REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                support_count INTEGER NOT NULL DEFAULT 0,
                oppose_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                dispute_deadline TEXT NOT NULL,
                resolved_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resolution_disputes (
                dispute_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                market_id TEXT NOT NULL,
                disputer_id TEXT NOT NULL,
                counter_outcome TEXT NOT NULL,
                evidence TEXT,
                bond_amount REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resolution_votes (
                vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                voter_id TEXT NOT NULL,
                vote TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oracle_data_cache (
                cache_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                query TEXT NOT NULL,
                result TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resolution_history (
                resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                final_outcome TEXT NOT NULL,
                method TEXT NOT NULL,
                confidence REAL NOT NULL,
                proposal_id TEXT,
                total_bonds REAL NOT NULL DEFAULT 0,
                resolved_at TEXT NOT NULL
            )
        """)

    def register_source(self, name: str, source_type: str,
                        confidence: float = 0.5, base_url: str = "",
                        api_key_env: str = "") -> Dict[str, Any]:
        """Register a new data source for oracle resolution.

        Args:
            name: Human-readable source name.
            source_type: Type of data source (api, price_feed, manual, etc.).
            confidence: Base confidence score (0.0-1.0).
            base_url: API endpoint URL.
            api_key_env: Environment variable name for API key.

        Returns:
            Source registration details.
        """
        source_id = f"src_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        conn.execute("""
            INSERT INTO oracle_sources
            (source_id, name, source_type, base_url, api_key_env,
             confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source_id, name, source_type, base_url, api_key_env,
              confidence, now))

        return {
            "source_id": source_id,
            "name": name,
            "type": source_type,
            "confidence": confidence,
        }

    def propose_resolution(self, market_id: str, proposed_outcome: str,
                           proposer_id: str, evidence: str = "",
                           bond_amount: float = 0,
                           confidence: float = None) -> Dict[str, Any]:
        """Propose a resolution for a market.

        Anyone can propose a resolution by posting a bond.
        If not disputed within the dispute period, it's accepted.

        Args:
            market_id: Market to resolve.
            proposed_outcome: The proposed winning outcome.
            proposer_id: Proposer's user ID.
            evidence: Supporting evidence (URLs, data, etc.).
            bond_amount: USDC bond amount.
            confidence: Confidence score (auto-calculated if None).

        Returns:
            Proposal details.
        """
        proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        deadline = (
            datetime.now(timezone.utc) + timedelta(hours=self.DISPUTE_PERIOD_HOURS)
        ).isoformat()

        if confidence is None:
            confidence = self._calculate_confidence(market_id, proposed_outcome)

        conn = self._get_conn()
        conn.execute("""
            INSERT INTO resolution_proposals
            (proposal_id, market_id, proposed_outcome, proposer_id,
             evidence, bond_amount, confidence, status,
             created_at, dispute_deadline)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """, (proposal_id, market_id, proposed_outcome, proposer_id,
              evidence, bond_amount, confidence, now, deadline))

        return {
            "proposal_id": proposal_id,
            "market_id": market_id,
            "proposed_outcome": proposed_outcome,
            "confidence": round(confidence, 4),
            "bond_amount": bond_amount,
            "dispute_deadline": deadline,
            "status": "PENDING",
        }

    def dispute_proposal(self, proposal_id: str, disputer_id: str,
                         counter_outcome: str, evidence: str = "",
                         bond_amount: float = None) -> Dict[str, Any]:
        """Dispute a resolution proposal.

        Requires a bond equal to or greater than the original proposal.
        """
        conn = self._get_conn()
        proposal = self._get_proposal(conn, proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")
        if proposal["status"] != "PENDING":
            raise ValueError("Can only dispute PENDING proposals")

        bond = bond_amount or max(proposal["bond_amount"], self.DISPUTE_BOND_USDC)
        if bond < self.DISPUTE_BOND_USDC:
            raise ValueError(f"Minimum dispute bond is {self.DISPUTE_BOND_USDC} USDC")

        dispute_id = f"disp_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT INTO resolution_disputes
            (dispute_id, proposal_id, market_id, disputer_id,
             counter_outcome, evidence, bond_amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (dispute_id, proposal_id, proposal["market_id"],
              disputer_id, counter_outcome, evidence, bond, now))

        conn.execute("""
            UPDATE resolution_proposals SET status = 'DISPUTED'
            WHERE proposal_id = ?
        """, (proposal_id,))

        return {
            "dispute_id": dispute_id,
            "proposal_id": proposal_id,
            "counter_outcome": counter_outcome,
            "bond_amount": bond,
            "status": "DISPUTED",
        }

    def vote_on_proposal(self, proposal_id: str, voter_id: str,
                         vote: str, weight: float = 1.0) -> Dict[str, Any]:
        """Vote for or against a disputed proposal.

        Args:
            proposal_id: Proposal to vote on.
            voter_id: Voter's user ID.
            vote: "SUPPORT" or "OPPOSE".
            weight: Vote weight (based on reputation/stake).
        """
        if vote not in ("SUPPORT", "OPPOSE"):
            raise ValueError("Vote must be SUPPORT or OPPOSE")

        conn = self._get_conn()
        proposal = self._get_proposal(conn, proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO resolution_votes
            (proposal_id, voter_id, vote, weight, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (proposal_id, voter_id, vote, weight, now))

        if vote == "SUPPORT":
            conn.execute("""
                UPDATE resolution_proposals SET support_count = support_count + ?
                WHERE proposal_id = ?
            """, (weight, proposal_id))
        else:
            conn.execute("""
                UPDATE resolution_proposals SET oppose_count = oppose_count + ?
                WHERE proposal_id = ?
            """, (weight, proposal_id))

        return {"proposal_id": proposal_id, "vote": vote, "weight": weight}

    def finalize_resolution(self, market_id: str) -> Dict[str, Any]:
        """Finalize a resolution after the dispute period.

        If undisputed, auto-confirms the proposal.
        If disputed, uses voting to determine outcome.
        """
        conn = self._get_conn()
        proposals = conn.execute("""
            SELECT * FROM resolution_proposals
            WHERE market_id = ? AND status IN ('PENDING', 'DISPUTED')
            ORDER BY created_at DESC
        """, (market_id,)).fetchall()

        if not proposals:
            raise ValueError(f"No pending proposals for market {market_id}")

        proposal = proposals[0]
        proposal_id = proposal[0]
        proposed_outcome = proposal[2]
        status = proposal[7]

        # Check if dispute period expired
        deadline = datetime.fromisoformat(
            proposal[9].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        if now < deadline:
            raise ValueError("Dispute period has not expired yet")

        if status == "PENDING":
            # Undisputed: accept proposed outcome
            final_outcome = proposed_outcome
            method = "UNDISPUTED"
            confidence = proposal[6]
        else:
            # Disputed: use voting
            support = proposal[8]
            oppose = proposal[9] if len(proposal) > 9 else 0
            if support > oppose:
                final_outcome = proposed_outcome
                method = "VOTE_CONFIRMED"
                confidence = support / max(support + oppose, 1)
            else:
                # Get counter outcome from dispute
                dispute = conn.execute("""
                    SELECT counter_outcome FROM resolution_disputes
                    WHERE proposal_id = ? ORDER BY created_at DESC LIMIT 1
                """, (proposal_id,)).fetchone()
                final_outcome = dispute[0] if dispute else proposed_outcome
                method = "VOTE_OVERTURNED"
                confidence = oppose / max(support + oppose, 1)

        now_str = now.isoformat()
        conn.execute("""
            UPDATE resolution_proposals SET status = 'CONFIRMED',
            resolved_at = ? WHERE proposal_id = ?
        """, (now_str, proposal_id))

        conn.execute("""
            INSERT INTO resolution_history
            (market_id, final_outcome, method, confidence,
             proposal_id, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (market_id, final_outcome, method, round(confidence, 4),
              proposal_id, now_str))

        return {
            "market_id": market_id,
            "final_outcome": final_outcome,
            "method": method,
            "confidence": round(confidence, 4),
            "proposal_id": proposal_id,
        }

    def get_pending_resolutions(self) -> List[Dict]:
        """Get all pending resolution proposals."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM resolution_proposals
            WHERE status IN ('PENDING', 'DISPUTED')
            ORDER BY created_at ASC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_resolution_history(self, market_id: str) -> List[Dict]:
        """Get resolution history for a market."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM resolution_history
            WHERE market_id = ?
            ORDER BY resolved_at DESC
        """, (market_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_sources(self) -> List[Dict]:
        """List all registered data sources."""
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM oracle_sources WHERE is_active = 1"
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_proposal(self, conn, proposal_id: str) -> Optional[Dict]:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM resolution_proposals WHERE proposal_id = ?",
            (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def _calculate_confidence(self, market_id: str, outcome: str) -> float:
        """Calculate resolution confidence based on available data."""
        # In production, this would check actual data sources
        # For now, return a reasonable default
        return 0.75


if __name__ == "__main__":
    oracle = ResolutionOracle(db_path=":memory:")

    print("Registering data sources...")
    oracle.register_source("CoinGecko", "price_feed", 0.9, "https://api.coingecko.com")
    oracle.register_source("Chainlink", "price_feed", 0.95, "https://feeds.chain.link")
    oracle.register_source("Manual", "manual", 0.5)

    print("\nProposing resolution...")
    prop = oracle.propose_resolution(
        "btc_100k", "YES", "alice",
        evidence="BTC/USD reached $151,000 on CoinGecko at 2026-12-30",
        bond_amount=200, confidence=0.92
    )
    print(f"  Proposal: {prop['proposal_id']}")
    print(f"  Confidence: {prop['confidence']}")
    print(f"  Status: {prop['status']}")

    print("\nDisputing proposal...")
    disp = oracle.dispute_proposal(
        prop["proposal_id"], "bob", "NO",
        evidence="BTC only reached $148k, data from Binance confirms",
        bond_amount=200
    )
    print(f"  Dispute: {disp['dispute_id']}")

    print("\nVoting on proposal...")
    oracle.vote_on_proposal(prop["proposal_id"], "charlie", "SUPPORT", 2.0)
    oracle.vote_on_proposal(prop["proposal_id"], "dave", "SUPPORT", 1.5)
    oracle.vote_on_proposal(prop["proposal_id"], "eve", "OPPOSE", 1.0)

    print("\nSources:")
    for src in oracle.get_sources():
        print(f"  {src['name']}: confidence={src['confidence']}")

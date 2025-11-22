import uuid
import json

from dataclasses import dataclass, asdict
from typing import Dict, List

import requests
import streamlit as st
import pandas as pd

# -----------------------
# Config / Constants
# -----------------------

LIQ_THRESHOLD_BETA = 0.05  # 5% of IM
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com"


# -----------------------
# Data Models
# -----------------------

@dataclass
class Market:
    id: str
    name: str
    description: str
    current_p: float  # 0 to 1
    status: str = "OPEN"  # OPEN, SETTLED
    outcome: float = None  # None, 0.0, or 1.0


@dataclass
class Position:
    id: str
    user_id: str
    market_id: str
    side: str  # "LONG" (YES) or "SHORT" (NO)
    notional: float
    im_pct: float
    im_amount: float
    entry_p: float
    current_p: float
    pnl: float
    equity: float
    status: str = "OPEN"  # OPEN, LIQUIDATED, SETTLED


# -----------------------
# Polymarket Integration
# -----------------------

def fetch_polymarket_markets(
    max_markets: int = 20,
    require_yes_no: bool = True,
    only_open: bool = True,
) -> List[dict]:
    """
    Fetch markets from Polymarket Gamma API.

    - max_markets: how many markets to keep
    - require_yes_no: keep only binary markets with ['Yes','No'] outcomes
    - only_open: keep only active & not closed markets
    """
    resp = requests.get(f"{POLYMARKET_GAMMA_URL}/markets", timeout=10)
    resp.raise_for_status()
    data = resp.json()

    selected: List[dict] = []
    for m in data:
        if only_open:
            if not m.get("active", False):
                continue
            if m.get("closed", False):
                continue

        if require_yes_no:
            try:
                outs = json.loads(m.get("outcomes", "[]"))
            except json.JSONDecodeError:
                continue
            if len(outs) != 2:
                continue

        selected.append(m)
        if len(selected) >= max_markets:
            break

    return selected


def polymarket_market_to_bube(m: dict) -> dict:
    """
    Convert a Polymarket market JSON object into a BUBE 'Market' dict.

    We treat the first outcome in outcomePrices as 'YES' probability P.
    """
    try:
        prices = json.loads(m.get("outcomePrices", "[]"))
        p_yes = float(prices[0]) if prices else 0.0
    except (json.JSONDecodeError, ValueError, IndexError):
        p_yes = 0.0

    description = m.get("description") or ""

    return {
        "id": f"poly-{m['id']}",  # prefix to avoid clashing with local UUIDs
        "name": m.get("question", f"Polymarket {m['id']}"),
        "description": description,
        "current_p": p_yes,
        "status": "OPEN",
        "outcome": None,
        "source": "polymarket",
        "poly_id": m["id"],
        "poly_slug": m.get("slug"),
    }


# -----------------------
# Core PD Math
# -----------------------

def pd_pnl(notional: float, p_entry: float, p_current: float, side: str) -> float:
    """
    P&L = Notional * (P_current - P_entry) * side
    side: 'LONG' (YES) or 'SHORT' (NO)
    """
    s = 1.0 if side.upper() == "LONG" else -1.0
    delta_p = p_current - p_entry
    return notional * delta_p * s


def compute_equity(im_amount: float, pnl: float) -> float:
    return im_amount + pnl


def is_liquidation(equity: float, im_amount: float) -> bool:
    return equity <= LIQ_THRESHOLD_BETA * im_amount


# -----------------------
# State Initialisation
# -----------------------

def init_state():
    if "users" not in st.session_state:
        st.session_state.users = {}

    if "markets" not in st.session_state:
        st.session_state.markets = {}

    if "positions" not in st.session_state:
        st.session_state.positions = []

    # Seed a demo user
    if not st.session_state.users:
        uid = str(uuid.uuid4())
        st.session_state.users[uid] = "demo_trader"

    # Seed markets – first try Polymarket; if it fails, fallback to hardcoded examples
    if not st.session_state.markets:
        try:
            poly_markets = fetch_polymarket_markets(max_markets=10)
            if poly_markets:
                for pm in poly_markets:
                    bm = polymarket_market_to_bube(pm)
                    st.session_state.markets[bm["id"]] = bm
            else:
                raise RuntimeError("No Polymarket markets returned")
        except Exception as e:
            st.sidebar.warning(
                f"Polymarket API unavailable ({e}). Using local demo markets instead."
            )
            m1 = Market(
                id=str(uuid.uuid4()),
                name="BTC hits $100,000 by 31 Dec 2026",
                description="YES if BTC trades at or above $100k on any major exchange before end of 2026.",
                current_p=0.62,
            )
            m2 = Market(
                id=str(uuid.uuid4()),
                name="Trump wins US Election 2028",
                description="YES if Trump wins the 2028 US Presidential election.",
                current_p=0.41,
            )
            st.session_state.markets[m1.id] = asdict(m1)
            st.session_state.markets[m2.id] = asdict(m2)


# -----------------------
# Engine: Update & Settle
# -----------------------

def update_positions_for_market(market_id: str):
    markets = st.session_state.markets
    positions = st.session_state.positions
    current_p = markets[market_id]["current_p"]

    for pos in positions:
        if pos["market_id"] != market_id:
            continue
        if pos["status"] != "OPEN":
            continue

        pnl = pd_pnl(
            pos["notional"],
            pos["entry_p"],
            current_p,
            pos["side"],
        )
        equity = compute_equity(pos["im_amount"], pnl)
        pos["pnl"] = pnl
        pos["equity"] = equity
        pos["current_p"] = current_p

        if is_liquidation(equity, pos["im_amount"]):
            pos["status"] = "LIQUIDATED"


def settle_market(market_id: str, outcome: float):
    """
    outcome: 0.0 or 1.0
    """
    markets = st.session_state.markets
    positions = st.session_state.positions

    markets[market_id]["status"] = "SETTLED"
    markets[market_id]["outcome"] = outcome

    for pos in positions:
        if pos["market_id"] != market_id:
            continue
        if pos["status"] == "SETTLED":
            continue

        if pos["status"] == "LIQUIDATED":
            # Keep last P&L, just mark as SETTLED
            pos["status"] = "SETTLED"
            continue

        final_pnl = pd_pnl(
            pos["notional"],
            pos["entry_p"],
            outcome,
            pos["side"],
        )
        pos["pnl"] = final_pnl
        pos["equity"] = compute_equity(pos["im_amount"], final_pnl)
        pos["current_p"] = outcome
        pos["status"] = "SETTLED"


# -----------------------
# Streamlit UI
# -----------------------

def main():
    st.set_page_config(page_title="BUBE PD + Polymarket MVP", layout="wide")
    init_state()

    st.title("BUBE Protocol – PD Derivatives on Polymarket (Testnet MVP)")

    # Sidebar: user management
    st.sidebar.header("Trader")
    users = st.session_state.users
    user_ids = list(users.keys())
    user_labels = [users[uid] for uid in user_ids]
    selected_idx = st.sidebar.selectbox(
        "Select user", range(len(user_ids)), format_func=lambda i: user_labels[i]
    )
    current_user_id = user_ids[selected_idx]
    current_username = users[current_user_id]
    st.sidebar.write(f"Current user: **{current_username}**")

    with st.sidebar.expander("Add new user"):
        new_name = st.text_input("Username", key="new_user_name")
        if st.button("Create user"):
            if new_name.strip():
                uid = str(uuid.uuid4())
                st.session_state.users[uid] = new_name.strip()
                st.success(f"User '{new_name}' created. Select in dropdown above.")
            else:
                st.error("Username cannot be empty.")

    markets = st.session_state.markets
    positions = st.session_state.positions

    tab1, tab2, tab3, tab4 = st.tabs(
        ["📊 Markets", "🧾 Open Position", "📈 Positions & Risk", "⚙ Admin"]
    )

    # --- Markets tab ---
    with tab1:
        st.subheader("Available Prediction Markets (Polymarket-backed PD Derivatives)")
        if not markets:
            st.warning("No markets available.")
        else:
            for mid, m in markets.items():
                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    src = m.get("source", "local")
                    st.markdown(f"**{m['name']}**  \nSource: `{src}`")
                    if m.get("poly_slug"):
                        st.caption(f"Polymarket slug: {m['poly_slug']}")
                    st.caption(m["description"])
                with col2:
                    st.metric("Current Probability (YES)", f"{m['current_p']:.2%}")
                with col3:
                    st.text(f"Status: {m['status']}")
                    if m["status"] == "SETTLED":
                        st.text(f"Outcome: {m['outcome']:.0f}")

    # --- Open Position tab ---
    with tab2:
        st.subheader("Open New PD Derivatives Position")

        if not markets:
            st.warning("No markets available.")
        else:
            market_ids = list(markets.keys())
            market_labels = [markets[mid]["name"] for mid in market_ids]
            m_idx = st.selectbox(
                "Select market", range(len(market_ids)), format_func=lambda i: market_labels[i]
            )
            selected_market_id = market_ids[m_idx]
            m = markets[selected_market_id]
            st.write(f"Current probability (YES): **{m['current_p']:.2%}**")

            side = st.radio("Side", ["LONG (YES)", "SHORT (NO)"])
            side_value = "LONG" if "LONG" in side else "SHORT"

            notional = st.number_input(
                "Notional (e.g., 1,000,000)",
                min_value=0.0,
                value=1_000_000.0,
                step=100_000.0,
            )
            im_pct_view = st.slider(
                "Initial Margin % of Notional",
                min_value=2.0,
                max_value=100.0,
                value=10.0,
                step=1.0,
            )
            im_pct = im_pct_view / 100.0

            if st.button("Open Position"):
                if m["status"] != "OPEN":
                    st.error("Cannot open position on a settled market.")
                elif notional <= 0:
                    st.error("Notional must be > 0.")
                else:
                    im_amount = notional * im_pct
                    entry_p = m["current_p"]
                    pnl = 0.0
                    equity = im_amount

                    pos = Position(
                        id=str(uuid.uuid4()),
                        user_id=current_user_id,
                        market_id=selected_market_id,
                        side=side_value,
                        notional=notional,
                        im_pct=im_pct,
                        im_amount=im_amount,
                        entry_p=entry_p,
                        current_p=entry_p,
                        pnl=pnl,
                        equity=equity,
                    )
                    st.session_state.positions.append(asdict(pos))
                    st.success(
                        f"Opened {side_value} position on '{m['name']}' with notional {notional:,.0f}"
                    )

    # --- Positions & Risk tab ---
    with tab3:
        st.subheader("Positions & Risk Overview")

        user_positions = [p for p in positions if p["user_id"] == current_user_id]
        if not user_positions:
            st.info("No positions yet for this user.")
        else:
            # Recompute latest P&L/equity based on current market probabilities
            for mid in markets.keys():
                update_positions_for_market(mid)

            rows = []
            for p in user_positions:
                m = markets[p["market_id"]]
                rows.append(
                    {
                        "Market": m["name"],
                        "Side": p["side"],
                        "Status": p["status"],
                        "Entry P": f"{p['entry_p']:.2%}",
                        "Current P": f"{p['current_p']:.2%}",
                        "Notional": p["notional"],
                        "IM Amount": p["im_amount"],
                        "P&L": p["pnl"],
                        "Equity": p["equity"],
                    }
                )

            df = pd.DataFrame(rows)
            st.dataframe(
                df.style.format(
                    {
                        "Notional": "{:,.0f}",
                        "IM Amount": "{:,.0f}",
                        "P&L": "{:,.0f}",
                        "Equity": "{:,.0f}",
                    }
                )
            )

    # --- Admin tab ---
    with tab4:
        st.subheader("Admin Controls (Testnet)")

        st.markdown("⚠️ Testnet only – admin simulates oracle & settlement.")

        market_ids = list(markets.keys())
        market_labels = [markets[mid]["name"] for mid in market_ids]
        if market_ids:
            a_idx = st.selectbox(
                "Select market to administer",
                range(len(market_ids)),
                format_func=lambda i: market_labels[i],
                key="admin_market_select",
            )
            adm_mid = market_ids[a_idx]
            adm_m = markets[adm_mid]

            st.write(f"**{adm_m['name']}**")
            st.write(f"Current probability: **{adm_m['current_p']:.2%}**")
            st.write(f"Status: **{adm_m['status']}**")

            # Refresh from Polymarket (if linked)
            st.markdown("### Refresh Probability from Polymarket (if linked)")
            if adm_m.get("source") == "polymarket":
                if st.button("Pull latest Polymarket price", key="refresh_poly"):
                    try:
                        poly_markets = fetch_polymarket_markets(max_markets=200)
                        poly_by_id = {m["id"]: m for m in poly_markets}
                        poly_id = adm_m.get("poly_id")
                        if poly_id in poly_by_id:
                            pm = poly_by_id[poly_id]
                            bm = polymarket_market_to_bube(pm)
                            st.session_state.markets[adm_mid]["current_p"] = bm["current_p"]
                            st.session_state.markets[adm_mid]["name"] = bm["name"]
                            st.session_state.markets[adm_mid]["description"] = bm["description"]
                            update_positions_for_market(adm_mid)
                            st.success(f"Updated from Polymarket: P = {bm['current_p']:.2%}")
                        else:
                            st.warning("Matching Polymarket market not found in latest API response.")
                    except Exception as e:
                        st.error(f"Error refreshing from Polymarket: {e}")
            else:
                st.info("This market is not linked to Polymarket (source != 'polymarket').")

            # Manual probability update (what-if)
            st.markdown("### Manual Probability Update (What-if)")
            new_p = st.slider(
                "New probability",
                min_value=0.0,
                max_value=1.0,
                value=float(adm_m["current_p"]),
                step=0.01,
            )
            if st.button("Apply manual probability", key="manual_prob"):
                st.session_state.markets[adm_mid]["current_p"] = float(new_p)
                update_positions_for_market(adm_mid)
                st.success(f"Updated probability to {new_p:.2%} and recomputed P&L.")

            # Settlement
            st.markdown("### Settle Market")
            outcome = st.selectbox("Outcome (final)", [0.0, 1.0])
            if st.button("Settle market with outcome"):
                settle_market(adm_mid, outcome)
                st.success(f"Market settled with outcome {outcome:.0f}.")
        else:
            st.info("No markets to administer.")


if __name__ == "__main__":
    main()

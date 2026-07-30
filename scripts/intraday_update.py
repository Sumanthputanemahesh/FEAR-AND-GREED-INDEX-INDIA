"""
India Fear & Greed Index — intraday live estimate.

Run every hour DURING NSE trading hours (see .github/workflows/intraday.yml).
This is deliberately separate from build_india_fgi.py / publish_latest.py
and NEVER touches india_fgi_core.csv, india_fgi_extended.csv, or any other
file those scripts own — this script only writes docs/data/intraday.json, a
same-day scratch file that gets reset at the start of each new trading day.

Why only 3 of the 6 components can honestly be "live":
  - Momentum, Volatility, Safe Haven only need 1-2 tickers each, so they can
    be recomputed cheaply and safely every hour from a fresh yfinance pull.
    (India's Volatility is simpler than the UK's — it's the real India VIX
    level itself, not a realised-vol calculation, so it's just a live quote.)
  - Strength/Breadth need ~48 constituent tickers — refetching that hourly
    risks Yahoo rate-limiting and isn't worth it for an intraday estimate.
  - Put/Call has no intraday feed: NSE's F&O bhavcopy is an end-of-day
    publication, period.
So Strength, Breadth, and Put/Call stay pinned at yesterday's official
close, and the composite here is clearly labelled as a live ESTIMATE, not
the official daily reading.

How "live" is detected: yfinance's daily-interval download for a ticker
includes today's still-forming bar once the market has started trading, and
that bar's Close keeps updating as the session progresses. If the last row
in the freshly downloaded series isn't dated today, the market hasn't
opened yet (or Yahoo hasn't started updating), and the run is skipped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).parent))
from build_india_fgi import (  # noqa: E402
    MA_WINDOW, SAFE_HAVEN_WINDOW,
    BOND_ETF_CANDIDATES, pct_rank_normalise, zone_for, dl,
)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "docs" / "data"
CORE_CSV = DATA_DIR / "india_fgi_core.csv"
EXT_CSV = DATA_DIR / "india_fgi_extended.csv"
INTRADAY_JSON = DATA_DIR / "intraday.json"

LIVE_COMPONENTS = {"momentum", "volatility", "safe_haven"}


def load_frozen_components():
    """Yesterday's officially-closed component scores, for whatever this
    script can't sensibly recompute live (Strength, Breadth, Put/Call).
    Strength/Breadth come from CORE specifically — EXTENDED can lag CORE by
    several days when NSE's bhavcopy fetch misses a day (see
    publish_latest.py), so pulling everything from EXTENDED would freeze
    Strength/Breadth at a staler value than necessary."""
    frozen = {}
    dates = {}

    if CORE_CSV.exists():
        core = pd.read_csv(CORE_CSV, index_col=0, parse_dates=True)
        if not core.empty:
            row = core.iloc[-1]
            for c in ("strength", "breadth"):
                if c in core.columns:
                    frozen[c] = float(row[c])
            dates["core"] = str(core.index[-1].date())

    if EXT_CSV.exists():
        ext = pd.read_csv(EXT_CSV, index_col=0, parse_dates=True)
        if not ext.empty:
            row = ext.iloc[-1]
            if "put_call" in ext.columns:
                frozen["put_call"] = float(row["put_call"])
            dates["extended"] = str(ext.index[-1].date())

    return frozen, dates


def main():
    px = dl("^NSEI", period="3y")
    if px is None:
        print("FATAL: could not fetch ^NSEI")
        return

    today_utc = datetime.now(timezone.utc).date()
    last_bar_date = px.index[-1].date()
    if last_bar_date != today_utc:
        print(f"Last ^NSEI bar is dated {last_bar_date}, not today ({today_utc}) — "
              "market hasn't started updating today's bar yet. Skipping.")
        return

    vix = dl("^INDIAVIX", period="3y")
    if vix is None:
        print("FATAL: could not fetch ^INDIAVIX")
        return

    bond_px = None
    for ticker in BOND_ETF_CANDIDATES:
        s = dl(ticker, period="3y")
        if s is not None and len(s) > 300:
            bond_px = s
            break
    if bond_px is None:
        print("FATAL: no bond ETF candidate returned usable history")
        return

    momentum_raw = (px - px.rolling(MA_WINDOW).mean()).dropna()
    eq_ret = px.pct_change(SAFE_HAVEN_WINDOW)
    bd_ret = bond_px.pct_change(SAFE_HAVEN_WINDOW)
    safe_haven_raw = (eq_ret - bd_ret).dropna()

    norm_momentum = float(pct_rank_normalise(momentum_raw).iloc[-1])
    norm_vol = 100 - float(pct_rank_normalise(vix).iloc[-1])  # inverted
    norm_safe_haven = float(pct_rank_normalise(safe_haven_raw).iloc[-1])

    if any(np.isnan(v) for v in (norm_momentum, norm_vol, norm_safe_haven)):
        print("FATAL: a live component came back NaN — not enough warm-up history yet")
        return

    frozen, frozen_dates = load_frozen_components()
    live_components = {
        "momentum": round(norm_momentum, 1),
        "volatility": round(norm_vol, 1),
        "safe_haven": round(norm_safe_haven, 1),
    }
    all_components = {**frozen, **live_components}

    composite = float(np.mean(list(all_components.values())))
    zone = zone_for(composite)

    entry = {
        "time": datetime.now(timezone.utc).strftime("%H:%M"),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "composite": round(composite, 2),
        "zone": zone,
        "nifty_close": float(px.iloc[-1]),
        "components": {k: round(v, 1) for k, v in all_components.items()},
        "live_components": sorted(LIVE_COMPONENTS),
        "frozen_as_of": frozen_dates,
    }

    existing = []
    if INTRADAY_JSON.exists():
        try:
            existing = json.loads(INTRADAY_JSON.read_text())
        except (json.JSONDecodeError, ValueError):
            existing = []

    today_str = str(today_utc)
    existing = [e for e in existing if e["timestamp"][:10] == today_str]
    existing.append(entry)

    INTRADAY_JSON.write_text(json.dumps(existing, indent=2))
    print(f"Wrote {INTRADAY_JSON} — {entry['time']} UTC: {entry['composite']} ({entry['zone']}), "
          f"{len(existing)} point(s) today")


if __name__ == "__main__":
    main()

"""
India FGI — yfinance data puller.

Pulls everything usable from yfinance for the India Fear & Greed Index,
including a workaround for components that would otherwise need fragile
NSE scraping:

  - Index level data:      Nifty 50, Nifty Bank, Nifty Midcap 100, Nifty Smallcap 100
  - Volatility:             India VIX
  - Constituent-level data: full Nifty 50 (or Nifty 500) stock prices, used to
                             self-compute:
                               * Market Breadth  (advances vs declines)
                               * Price Strength  (% of stocks at 52-week high vs low)
                             instead of scraping NSE's breadth/52W-high-low reports.
  - Bond proxy:             an India government bond ETF (if listed) as a rough
                             yield-direction proxy, since yfinance has no direct
                             G-Sec yield series for India.

Run this file directly to see exactly what yfinance returns for each ticker —
useful for a first "what's actually available" pass before wiring it into
the full FGI pipeline.

NOTE: this sandbox environment blocks outbound calls to Yahoo Finance, so
this script is UNTESTED from here. Run it on your own machine or inside the
GitHub Action (which will have normal internet access) and check the printed
output / any tracebacks.
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT_DIR = Path(__file__).parent / "data" / "yf_raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Ticker universe
# ---------------------------------------------------------------------------

INDEX_TICKERS = {
    "nifty50": "^NSEI",
    "nifty_bank": "^NSEBANK",
    "nifty_midcap100": "NIFTY_MIDCAP_100.NS",   # availability on yfinance not guaranteed — verify
    "nifty_smallcap100": "NIFTY_SMLCAP_100.NS",  # availability on yfinance not guaranteed — verify
    "india_vix": "^INDIAVIX",
}

# Nifty 50 constituents (as of mid-2026 — verify/update periodically, NSE
# reshuffles the index twice a year). .NS suffix = NSE listing on Yahoo Finance.
NIFTY50_CONSTITUENTS = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BPCL.NS", "BHARTIARTL.NS",
    "BRITANNIA.NS", "CIPLA.NS", "COALINDIA.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS",
    "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "ITC.NS",
    "INDUSINDBK.NS", "INFY.NS", "JSWSTEEL.NS", "KOTAKBANK.NS", "LTIM.NS",
    "LT.NS", "M&M.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS",
    "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS", "SBIN.NS",
    "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS", "TATAMOTORS.NS", "TATASTEEL.NS",
    "TECHM.NS", "TITAN.NS", "UPL.NS", "ULTRACEMCO.NS", "WIPRO.NS",
]

BOND_PROXY_TICKERS = {
    # yfinance has no direct India 10Y G-Sec yield series. These are candidate
    # ETF/instrument proxies — verify each actually returns data before relying
    # on it; delisted/renamed tickers are common and will silently return empty.
    "india_gsec_etf_candidate": "0P0001A0DL.BO",  # placeholder — VERIFY on your machine
}


def pull_index_series(period="max", interval="1d") -> dict:
    results = {}
    for name, ticker in INDEX_TICKERS.items():
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False)
            if df.empty:
                results[name] = {"ticker": ticker, "status": "EMPTY", "rows": 0}
            else:
                out_path = OUT_DIR / f"{name}.csv"
                df.to_csv(out_path)
                results[name] = {
                    "ticker": ticker,
                    "status": "OK",
                    "rows": len(df),
                    "date_range": [str(df.index.min()), str(df.index.max())],
                    "saved_to": str(out_path),
                }
        except Exception as e:  # noqa: BLE001 — diagnostic script, want to see every failure
            results[name] = {"ticker": ticker, "status": "ERROR", "error": f"{type(e).__name__}: {e}"}
        time.sleep(0.5)  # be polite to Yahoo's endpoint, avoid rate-limit
    return results


def pull_constituent_prices(period="max", interval="1d") -> dict:
    """
    Pull daily closes for all Nifty 50 constituents in one batch call.
    Used downstream to self-compute breadth and 52-week high/low counts
    instead of scraping NSE reports.
    """
    try:
        data = yf.download(
            NIFTY50_CONSTITUENTS, period=period, interval=interval,
            progress=False, group_by="ticker", threads=True,
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "error": f"{type(e).__name__}: {e}"}

    if data.empty:
        return {"status": "EMPTY", "n_tickers_requested": len(NIFTY50_CONSTITUENTS)}

    out_path = OUT_DIR / "nifty50_constituents_close.csv"
    # Extract just Close prices per ticker into a wide DataFrame
    try:
        closes = pd.DataFrame({t: data[t]["Close"] for t in NIFTY50_CONSTITUENTS if t in data.columns.get_level_values(0)})
        closes.to_csv(out_path)
        missing = [t for t in NIFTY50_CONSTITUENTS if t not in closes.columns or closes[t].dropna().empty]
        return {
            "status": "OK",
            "n_tickers_requested": len(NIFTY50_CONSTITUENTS),
            "n_tickers_returned": len(closes.columns),
            "missing_or_empty": missing,
            "date_range": [str(closes.index.min()), str(closes.index.max())],
            "saved_to": str(out_path),
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "PARSE_ERROR", "error": f"{type(e).__name__}: {e}"}


def compute_breadth_and_strength_from_constituents() -> dict:
    """
    Self-computed substitutes for NSE's breadth and 52-week-high/low reports,
    using the constituent close-price file written by pull_constituent_prices.
    """
    path = OUT_DIR / "nifty50_constituents_close.csv"
    if not path.exists():
        return {"status": "SKIPPED", "reason": "constituent price file not found — run pull_constituent_prices first"}

    df = pd.read_csv(path, index_col=0, parse_dates=True)

    # Breadth: daily advances vs declines across the 50 constituents
    daily_ret = df.pct_change()
    advances = (daily_ret > 0).sum(axis=1)
    declines = (daily_ret < 0).sum(axis=1)
    net_adv_decl = (advances - declines)
    breadth_ema10 = net_adv_decl.ewm(span=10, min_periods=5).mean()

    # Strength: % of constituents at a 252-day high vs 252-day low, each day
    rolling_high = df.rolling(252, min_periods=60).max()
    rolling_low = df.rolling(252, min_periods=60).min()
    at_high = (df >= rolling_high).sum(axis=1)
    at_low = (df <= rolling_low).sum(axis=1)
    denom = (at_high + at_low)
    # When no stock hit a new high or low that day, treat as neutral (0.5)
    # rather than NaN, so the daily series stays complete for normalisation.
    strength_ratio = (at_high / denom.replace(0, pd.NA)).fillna(0.5)

    out = pd.DataFrame({
        "net_adv_decl": net_adv_decl,
        "breadth_ema10": breadth_ema10,
        "pct_at_52w_high": at_high / len(df.columns) * 100,
        "pct_at_52w_low": at_low / len(df.columns) * 100,
        "strength_ratio": strength_ratio,
    }).dropna(how="all")

    out_path = OUT_DIR / "breadth_and_strength_selfcomputed.csv"
    out.to_csv(out_path)

    return {
        "status": "OK",
        "rows": len(out),
        "date_range": [str(out.index.min()), str(out.index.max())],
        "latest": out.iloc[-1].to_dict() if not out.empty else None,
        "saved_to": str(out_path),
    }


def main():
    print("=== 1. Index-level tickers ===")
    index_results = pull_index_series()
    print(json.dumps(index_results, indent=2))

    print("\n=== 2. Nifty 50 constituent prices (for self-computed breadth/strength) ===")
    const_results = pull_constituent_prices()
    print(json.dumps(const_results, indent=2))

    print("\n=== 3. Self-computed breadth & strength ===")
    bs_results = compute_breadth_and_strength_from_constituents()
    print(json.dumps(bs_results, indent=2, default=str))

    summary = {
        "index_tickers": index_results,
        "constituent_pull": const_results,
        "breadth_strength": bs_results,
    }
    summary_path = OUT_DIR / "run_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nFull run summary written to {summary_path}")


if __name__ == "__main__":
    main()

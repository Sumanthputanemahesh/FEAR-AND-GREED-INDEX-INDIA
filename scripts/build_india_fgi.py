"""
India Fear & Greed Index — build script.

Run:  python3 build_india_fgi.py

Computes every component that can be derived from yfinance, and PROBES the
uncertain sources (bond ETFs for Safe Haven, NSE bhavcopy for Put/Call) so a
single run tells us exactly what is available on your machine.

Outputs (into ./data/):
  india_fgi_components_raw.csv   raw component series, pre-normalisation
  india_fgi_normalised.csv       0-100 normalised sub-scores + composite
  india_fgi_history.json         composite history for the website frontend
  build_report.json              what worked, what failed, and why

Methodology follows the verified Germany workbook conventions:
  - Normalisation: rolling 252-day percentile rank,
        (count of values BELOW current) / (window - 1) * 100
  - Volatility, Put/Call and Credit Spread are INVERTED (100 - score)
  - Strength uses 0.5 when both highs and lows are zero
  - Composite = equal-weighted mean of available normalised scores
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).parent.parent
OUT_DIR = BASE_DIR / "docs" / "data"
RAW_DIR = OUT_DIR / "yf_raw"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

ROLL_WINDOW = 252
MA_WINDOW = 125
SAFE_HAVEN_WINDOW = 20

# Zone boundaries. The workbook legend reads "0-25 / 26-45 / 46-55 / 56-75 /
# 76-100", which is written for whole-number scores. Our composite is
# continuous, so we use half-open upper bounds to avoid gaps that would leave
# e.g. 45.5 unclassified.
ZONES = [
    (0.0, 25.0, "Extreme Fear"),
    (25.0, 45.0, "Fear"),
    (45.0, 55.0, "Neutral"),
    (55.0, 75.0, "Greed"),
    (75.0, 100.0, "Extreme Greed"),
]

# Components where a HIGH raw value means FEAR -> invert after normalising.
INVERTED = {"volatility", "put_call", "credit_spread"}

NIFTY50_CONSTITUENTS = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BPCL.NS", "BHARTIARTL.NS",
    "BRITANNIA.NS", "CIPLA.NS", "COALINDIA.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS",
    "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "ITC.NS",
    "INDUSINDBK.NS", "INFY.NS", "JSWSTEEL.NS", "KOTAKBANK.NS",
    "LT.NS", "M&M.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS",
    "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS", "SBIN.NS",
    "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS", "TATASTEEL.NS",
    "TECHM.NS", "TITAN.NS", "UPL.NS", "ULTRACEMCO.NS", "WIPRO.NS",
]

# Candidate bond instruments for the Safe Haven leg. We try each in order and
# use the first that returns a usable history. None of these are verified —
# the run report will tell us which (if any) actually work.
# VERIFIED against a live run (2026-07-24). Rows/start dates are what Yahoo
# actually returned. We select the candidate with the LONGEST usable history
# among those with meaningful duration.
#
# LIQUIDBEES is deliberately EXCLUDED despite having the longest history
# (2009): it is a liquid/overnight fund with near-zero duration, so its price
# barely responds to rate moves. Using it would make the Safe Haven component
# collapse into "Nifty 20d return", double-counting momentum rather than
# measuring equity-vs-bond rotation.
BOND_ETF_CANDIDATES = [
    "LTGILTBEES.NS",   # Long-term gilt  - from 2018, high duration. BEST.
    "GILT5YBEES.NS",   # Nippon 5Y Gilt  - from 2021, medium duration.
    "EBBETF0433.NS",   # Bharat Bond 2033 - from 2025, too short for now.
    # "SETFGILT.NS",   # 404 on Yahoo - does not exist.
    # "LIQUIDBEES.NS", # Excluded: ~zero duration, see note above.
]

report: dict = {"generated_at": datetime.now().isoformat(), "steps": {}}


def log(step: str, payload: dict):
    report["steps"][step] = payload
    print(f"\n--- {step} ---")
    print(json.dumps(payload, indent=2, default=str))


def pct_rank_normalise(series: pd.Series, window: int = ROLL_WINDOW) -> pd.Series:
    """
    Rolling percentile rank, matching the verified workbook formula exactly:
        (count of values strictly BELOW current) / (window - 1) * 100

    NOT pandas .rank(pct=True) — that uses average ranks and divides by N.
    """
    def _rank(vals):
        current = vals[-1]
        return (vals < current).sum() / (len(vals) - 1) * 100

    return series.rolling(window).apply(_rank, raw=True)


def zone_for(score: float) -> str:
    """
    Zone label. Boundary convention matches build_uk_dual_index.py exactly
    (upper-inclusive: <=25 Extreme Fear, <=45 Fear, <=55 Neutral, <=75 Greed,
    else Extreme Greed) so India scores are directly comparable to the UK
    and Germany indices.
    """
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "Unknown"
    if score <= 25:
        return "Extreme Fear"
    elif score <= 45:
        return "Fear"
    elif score <= 55:
        return "Neutral"
    elif score <= 75:
        return "Greed"
    return "Extreme Greed"


def dl(ticker: str, period: str = "max") -> pd.Series | None:
    """Download one ticker's Close series. Returns None on failure/empty."""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return close.dropna()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Component 1: Momentum  — Nifty 50 minus its 125-day MA
# ---------------------------------------------------------------------------
def build_momentum() -> pd.Series | None:
    px = dl("^NSEI")
    if px is None:
        log("momentum", {"status": "FAILED", "reason": "^NSEI returned no data"})
        return None
    mom = (px - px.rolling(MA_WINDOW).mean()).dropna()
    px.to_csv(RAW_DIR / "nifty50.csv")
    log("momentum", {"status": "OK", "rows": len(mom),
                     "range": [str(mom.index.min().date()), str(mom.index.max().date())],
                     "latest": float(mom.iloc[-1])})
    return mom


# ---------------------------------------------------------------------------
# Component 2: Volatility — India VIX  (INVERTED)
# ---------------------------------------------------------------------------
def build_volatility() -> pd.Series | None:
    vix = dl("^INDIAVIX")
    if vix is None:
        log("volatility", {"status": "FAILED", "reason": "^INDIAVIX returned no data"})
        return None
    vix.to_csv(RAW_DIR / "india_vix.csv")
    log("volatility", {"status": "OK", "rows": len(vix),
                       "range": [str(vix.index.min().date()), str(vix.index.max().date())],
                       "latest": float(vix.iloc[-1])})
    return vix


# ---------------------------------------------------------------------------
# Components 3 & 4: Strength and Breadth — self-computed from constituents
# ---------------------------------------------------------------------------
def build_strength_and_breadth() -> tuple[pd.Series | None, pd.Series | None]:
    try:
        data = yf.download(NIFTY50_CONSTITUENTS, period="max", progress=False,
                           group_by="ticker", threads=True, auto_adjust=True)
    except Exception as e:
        log("constituents", {"status": "FAILED", "error": f"{type(e).__name__}: {e}"})
        return None, None

    if data is None or data.empty:
        log("constituents", {"status": "FAILED", "reason": "empty frame"})
        return None, None

    lvl0 = set(data.columns.get_level_values(0))
    closes = pd.DataFrame({
        t: data[t]["Close"] for t in NIFTY50_CONSTITUENTS if t in lvl0
    })
    closes = closes.dropna(axis=1, how="all")
    closes.to_csv(RAW_DIR / "nifty50_constituents_close.csv")

    # Breadth: net advances minus declines, 10-day EWM
    daily_ret = closes.pct_change(fill_method=None)
    net_adv_decl = (daily_ret > 0).sum(axis=1) - (daily_ret < 0).sum(axis=1)
    breadth = net_adv_decl.ewm(span=10, min_periods=5).mean()

    # Strength: highs / (highs + lows), 0.5 when both are zero
    roll_hi = closes.rolling(ROLL_WINDOW, min_periods=60).max()
    roll_lo = closes.rolling(ROLL_WINDOW, min_periods=60).min()
    at_hi = (closes >= roll_hi).sum(axis=1)
    at_lo = (closes <= roll_lo).sum(axis=1)
    denom = at_hi + at_lo
    strength = (at_hi / denom.replace(0, np.nan)).fillna(0.5)

    # Trim the leading period before rolling windows are meaningful
    strength = strength.loc[roll_hi.dropna(how="all").index]

    log("strength_and_breadth", {
        "status": "OK",
        "n_constituents_used": len(closes.columns),
        "n_requested": len(NIFTY50_CONSTITUENTS),
        "missing": [t for t in NIFTY50_CONSTITUENTS if t not in closes.columns],
        "breadth_rows": len(breadth.dropna()),
        "strength_rows": len(strength.dropna()),
        "breadth_latest": float(breadth.dropna().iloc[-1]),
        "strength_latest": float(strength.dropna().iloc[-1]),
    })
    return strength.dropna(), breadth.dropna()


# ---------------------------------------------------------------------------
# Component 5: Safe Haven — Nifty 20d return minus bond 20d return
# ---------------------------------------------------------------------------
def build_safe_haven() -> pd.Series | None:
    px = dl("^NSEI")
    if px is None:
        log("safe_haven", {"status": "FAILED", "reason": "no equity series"})
        return None

    probe = {}
    bond_px = None
    bond_used = None
    for cand in BOND_ETF_CANDIDATES:
        s = dl(cand)
        if s is not None and len(s) > ROLL_WINDOW:
            probe[cand] = {"rows": len(s), "from": str(s.index.min().date()), "USED": bond_used is None}
            if bond_px is None:
                bond_px, bond_used = s, cand
        else:
            probe[cand] = {"rows": 0 if s is None else len(s), "usable": False}
        time.sleep(0.3)

    if bond_px is None:
        log("safe_haven", {"status": "FAILED",
                           "reason": "no bond ETF candidate returned usable history",
                           "probe": probe,
                           "note": "Safe Haven will be excluded. Find a working NSE bond "
                                   "ETF ticker on Yahoo Finance and add it to BOND_ETF_CANDIDATES."})
        return None

    eq_ret = px.pct_change(SAFE_HAVEN_WINDOW)
    bd_ret = bond_px.pct_change(SAFE_HAVEN_WINDOW)
    sh = (eq_ret - bd_ret).dropna()

    log("safe_haven", {"status": "OK", "bond_ticker_used": bond_used,
                       "rows": len(sh),
                       "range": [str(sh.index.min().date()), str(sh.index.max().date())],
                       "latest": float(sh.iloc[-1]),
                       "probe": probe})
    return sh


# ---------------------------------------------------------------------------
# Component 6: Put/Call — NSE F&O bhavcopy  (INVERTED)
# ---------------------------------------------------------------------------
def _pcr_from_bhavcopy_bytes(zip_bytes: bytes) -> float | None:
    """
    Parse one day's NSE F&O UDiFF bhavcopy ZIP -> Nifty index-option PCR (OI).

    Verified against BhavCopy_NSE_FO_0_0_0_20260724_F_0000.csv:
      FinInstrmTp == 'IDO'  -> index options (STO=stock opt, IDF/STF=futures)
      TckrSymb   == 'NIFTY'
      OptnTp     == 'CE'/'PE'
      OpnIntrst  -> open interest
    """
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(name) as fh:
            df = pd.read_csv(fh)

    n = df[(df["TckrSymb"] == "NIFTY") & (df["FinInstrmTp"] == "IDO")]
    ce = n.loc[n["OptnTp"] == "CE", "OpnIntrst"].sum()
    pe = n.loc[n["OptnTp"] == "PE", "OpnIntrst"].sum()
    if ce <= 0:
        return None
    return float(pe) / float(ce)


def _bhavcopy_url(d: pd.Timestamp) -> str:
    return (
        "https://nsearchives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    )


def build_put_call(days_back: int = 780) -> pd.Series | None:
    """
    Build the Nifty Put/Call Ratio series from NSE F&O UDiFF bhavcopy files.

    One HTTP request per trading day, so the first run is slow. Results are
    cached to data/pcr_cache.csv and only missing dates are fetched on
    subsequent runs.

    INVERTED downstream: a high PCR means more puts = fear.
    """
    import requests

    cache_path = OUT_DIR / "pcr_cache.csv"
    cache: dict[str, float] = {}
    if cache_path.exists():
        prev = pd.read_csv(cache_path, index_col=0)
        cache = {str(k): float(v) for k, v in prev.iloc[:, 0].items()}

    end = pd.Timestamp.today().normalize()
    start = end - pd.Timedelta(days=days_back)
    wanted = pd.bdate_range(start, end)
    todo = [d for d in wanted if d.strftime("%Y-%m-%d") not in cache]

    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36"),
        "Accept": "*/*",
        "Referer": "https://www.nseindia.com/all-reports-derivatives",
    }
    sess = requests.Session()
    try:
        sess.get("https://www.nseindia.com/all-reports-derivatives",
                 headers=headers, timeout=20)
    except Exception:
        pass  # cookie priming is best-effort

    fetched = failed = 0
    print(f"\n[put_call] {len(cache)} cached, fetching {len(todo)} missing days...")
    for i, d in enumerate(todo, 1):
        try:
            r = sess.get(_bhavcopy_url(d), headers=headers, timeout=30)
            if r.status_code == 200 and r.content[:2] == b"PK":
                pcr = _pcr_from_bhavcopy_bytes(r.content)
                if pcr is not None:
                    cache[d.strftime("%Y-%m-%d")] = pcr
                    fetched += 1
            else:
                failed += 1          # holiday/weekend/not published
        except Exception:
            failed += 1
        if i % 25 == 0:
            print(f"  ...{i}/{len(todo)}  (ok={fetched} miss={failed})")
        time.sleep(0.4)              # be polite to NSE

    if not cache:
        log("put_call", {"status": "FAILED",
                         "reason": "no bhavcopy files could be downloaded",
                         "attempted": len(todo),
                         "hint": "NSE may be blocking this IP, or the URL pattern changed."})
        return None

    s = pd.Series(cache, dtype=float)
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    s.to_frame("pcr_oi").to_csv(cache_path)

    log("put_call", {
        "status": "OK",
        "rows": len(s),
        "newly_fetched": fetched,
        "days_unavailable": failed,
        "range": [str(s.index.min().date()), str(s.index.max().date())],
        "latest_pcr": round(float(s.iloc[-1]), 4),
        "note": "PCR is INVERTED when normalised (high PCR = fear).",
    })
    return s


# ---------------------------------------------------------------------------
# Component 7: Credit Spread — no free daily India HY-IG series  (INVERTED)
# ---------------------------------------------------------------------------
def build_credit_spread() -> pd.Series | None:
    log("credit_spread", {
        "status": "NOT_IMPLEMENTED",
        "reason": "India has no liquid free daily HY-IG OAS series. Genuine data "
                  "gap, not a coding gap.",
        "options": [
            "Drop permanently and document as a market-structure limitation",
            "Proxy with FBIL/CCIL AAA-vs-GSec spread (needs a scraper)",
            "Proxy with smallcap-vs-largecap relative performance (equity signal "
            "standing in for a credit signal - risks double-counting momentum)",
        ],
    })
    return None


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def main():
    print("Building India Fear & Greed Index...")
    print("(full history pull - this takes a few minutes)")

    raw: dict[str, pd.Series] = {}

    if (m := build_momentum()) is not None:
        raw["momentum"] = m
    if (v := build_volatility()) is not None:
        raw["volatility"] = v

    strength, breadth = build_strength_and_breadth()
    if strength is not None:
        raw["strength"] = strength
    if breadth is not None:
        raw["breadth"] = breadth

    if (sh := build_safe_haven()) is not None:
        raw["safe_haven"] = sh
    if (pc := build_put_call()) is not None:
        raw["put_call"] = pc
    if (cs := build_credit_spread()) is not None:
        raw["credit_spread"] = cs

    if not raw:
        print("\nFATAL: no components available.", file=sys.stderr)
        sys.exit(1)

    raw_df = pd.DataFrame(raw)
    raw_df.to_csv(OUT_DIR / "india_fgi_components_raw.csv")

    # Normalise each component, inverting the fear-positive ones
    norm = {}
    for name, series in raw.items():
        s = pct_rank_normalise(series)
        if name in INVERTED:
            s = 100 - s
        norm[name] = s

    # ---- Two indices, because component history lengths differ wildly ----
    #
    # Put/Call can only start mid-2025 (NSE's UDiFF bhavcopy format only exists
    # from July 2024, minus a 252-day normalisation warm-up). Requiring it would
    # truncate the whole index to ~1 year. So we publish both:
    #
    #   CORE     - the long-history components. Use this for any statistical
    #              work, backtesting, or regime analysis.
    #   EXTENDED - every available component including Put/Call. More complete
    #              picture of today, but a much shorter series.
    #
    # These are NOT interchangeable: a CORE score and an EXTENDED score for the
    # same day are computed from different component sets and will differ.
    CORE_COMPONENTS = ["momentum", "volatility", "strength", "breadth", "safe_haven"]

    def _assemble(cols: list[str], label: str) -> pd.DataFrame | None:
        cols = [c for c in cols if c in norm]
        if not cols:
            return None
        d = pd.DataFrame({c: norm[c] for c in cols}).dropna()
        if d.empty:
            return None
        d["composite"] = d[cols].mean(axis=1)
        d["zone"] = d["composite"].apply(zone_for)
        d.to_csv(OUT_DIR / f"india_fgi_{label}.csv")
        return d

    core_df = _assemble(CORE_COMPONENTS, "core")
    ext_df = _assemble(list(norm.keys()), "extended")

    if core_df is None and ext_df is None:
        print("\nFATAL: no overlapping dates after normalisation.", file=sys.stderr)
        sys.exit(1)

    log("INDEX_VERSIONS", {
        "core": {
            "components": [c for c in CORE_COMPONENTS if c in norm],
            "n_days": 0 if core_df is None else len(core_df),
            "range": None if core_df is None else
                     [str(core_df.index.min().date()), str(core_df.index.max().date())],
            "latest": None if core_df is None else
                      round(float(core_df["composite"].iloc[-1]), 2),
            "zone": None if core_df is None else core_df["zone"].iloc[-1],
        },
        "extended": {
            "components": list(norm.keys()),
            "n_days": 0 if ext_df is None else len(ext_df),
            "range": None if ext_df is None else
                     [str(ext_df.index.min().date()), str(ext_df.index.max().date())],
            "latest": None if ext_df is None else
                      round(float(ext_df["composite"].iloc[-1]), 2),
            "zone": None if ext_df is None else ext_df["zone"].iloc[-1],
        },
    })

    # The headline index is CORE - long history matters more than one extra
    # component for anything analytical.
    norm_df = core_df if core_df is not None else ext_df
    norm = {c: norm[c] for c in norm_df.columns if c in norm}
    norm_df.to_csv(OUT_DIR / "india_fgi_normalised.csv")

    # History JSON for the frontend. Written for BOTH index versions so the
    # site can toggle between them without a second fetch.
    def _to_history(d: pd.DataFrame | None) -> list:
        if d is None:
            return []
        comp_cols = [c for c in d.columns if c not in ("composite", "zone")]
        return [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "composite_score": round(float(row["composite"]), 2),
                "zone": row["zone"],
                "components": {c: round(float(row[c]), 2) for c in comp_cols},
            }
            for idx, row in d.iterrows()
        ]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "core": _to_history(core_df),
        "extended": _to_history(ext_df),
    }
    (OUT_DIR / "india_fgi_history.json").write_text(json.dumps(payload, indent=2))

    latest = norm_df.iloc[-1]
    summary = {
        "status": "OK",
        "components_used": list(norm.keys()),
        "n_components": len(norm),
        "date_range": [str(norm_df.index.min().date()), str(norm_df.index.max().date())],
        "n_days": len(norm_df),
        "latest_date": str(norm_df.index[-1].date()),
        "latest_score": round(float(latest["composite"]), 2),
        "latest_zone": latest["zone"],
        "latest_components": {c: round(float(latest[c]), 2) for c in norm.keys()},
        "zone_distribution_pct": (
            norm_df["zone"].value_counts(normalize=True).mul(100).round(1).to_dict()
        ),
    }
    log("FINAL", summary)

    (OUT_DIR / "build_report.json").write_text(json.dumps(report, indent=2, default=str))

    print("\n" + "=" * 60)
    print(f"  INDIA FEAR & GREED INDEX: {summary['latest_score']}  ({summary['latest_zone']})")
    print(f"  as of {summary['latest_date']}  |  {summary['n_components']} components")
    print("=" * 60)
    print(f"\nFiles written to {OUT_DIR}/")
    for f in ["india_fgi_components_raw.csv", "india_fgi_normalised.csv",
              "india_fgi_history.json", "build_report.json"]:
        print(f"  - {f}")


if __name__ == "__main__":
    main()

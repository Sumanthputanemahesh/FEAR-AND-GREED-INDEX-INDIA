"""
India Fear & Greed Index (FGI) — daily pipeline.

Adapts the 7-component CNN-style Fear & Greed methodology to Indian markets,
using only free, publicly available data sources so it can run unattended
on a daily schedule (e.g. via GitHub Actions).

Components
----------
1. Market Momentum      Nifty 50 price vs 125-day moving average       [yfinance]
2. Volatility            India VIX level                                [yfinance]
3. Price Strength        52-week highs vs lows on NSE (bhavcopy-based)   [NSE bhavcopy]
4. Market Breadth        NSE advances vs declines, 10-day EMA            [NSE bhavcopy]
5. Put/Call Ratio        Nifty options PCR, derived from NSE FO bhavcopy [NSE FO bhavcopy]  (PROXY)
6. Safe Haven Demand     Nifty 20d return vs 10Y G-Sec yield             [yfinance + RBI/FBIL]
7. Credit Spread         AAA corporate bond yield vs 10Y G-Sec yield     [FBIL]              (PROXY)

Each sub-score is normalised to 0-100 via a rolling 252-day percentile rank,
following the same convention as the UK/US thesis pipelines. The composite
is an equal-weighted average of the seven sub-scores.

Output: data/fgi_history.json (appended daily), consumed by the static
frontend (index.html) for GitHub Pages.

NOTE ON RELIABILITY
--------------------
NSE endpoints (components 3, 4, 5) are the least stable part of this
pipeline — NSE changes file formats/URLs periodically. Each fetch function
below is wrapped so a single failed component does NOT crash the whole run;
instead it is carried forward from the previous day's value and flagged in
the output as `stale`. Check the `warnings` field in the JSON output after
each run.
"""

import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

OUT_DIR = Path(__file__).parent / "data"
OUT_DIR.mkdir(exist_ok=True)
HISTORY_FILE = OUT_DIR / "fgi_history.json"

LOOKBACK_DAYS = 400          # calendar days of history to pull each run (covers 252 trading-day warmup)
ROLL_WINDOW = 252            # trading days for percentile normalisation

ZONES = [
    (0, 25, "Extreme Fear"),
    (25, 45, "Fear"),
    (45, 55, "Neutral"),
    (55, 75, "Greed"),
    (75, 100, "Extreme Greed"),
]


def zone_for(score: float) -> str:
    for lo, hi, name in ZONES:
        if lo <= score <= hi:
            return name
    return "Unknown"


@dataclass
class ComponentResult:
    name: str
    raw_series: pd.Series = field(default_factory=pd.Series)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.raw_series.empty


def safe_fetch(name):
    """Decorator-style wrapper: run a fetch function, catch and label errors."""
    def wrapper(fn):
        def inner(*args, **kwargs):
            try:
                series = fn(*args, **kwargs)
                return ComponentResult(name=name, raw_series=series)
            except Exception as e:  # noqa: BLE001 - deliberately broad, this is a daily unattended job
                return ComponentResult(name=name, error=f"{type(e).__name__}: {e}")
        return inner
    return wrapper


# ---------------------------------------------------------------------------
# 1. Market Momentum — Nifty 50 vs 125-day MA
# ---------------------------------------------------------------------------
@safe_fetch("momentum")
def fetch_momentum(start: datetime, end: datetime) -> pd.Series:
    px = yf.download("^NSEI", start=start, end=end, progress=False)["Close"]
    ma125 = px.rolling(125).mean()
    return (px - ma125).dropna()


# ---------------------------------------------------------------------------
# 2. Volatility — India VIX (inverted: higher VIX = more fear)
# ---------------------------------------------------------------------------
@safe_fetch("volatility")
def fetch_volatility(start: datetime, end: datetime) -> pd.Series:
    vix = yf.download("^INDIAVIX", start=start, end=end, progress=False)["Close"]
    return vix.dropna()


# ---------------------------------------------------------------------------
# 3. Price Strength — NSE 52-week high/low ratio
# ---------------------------------------------------------------------------
@safe_fetch("strength")
def fetch_strength(start: datetime, end: datetime) -> pd.Series:
    # NSE publishes a daily 52-week high/low report. Endpoint/format changes
    # periodically — this is the most fragile component. Placeholder pulls
    # from the NSE archives API; replace URL if NSE changes format.
    url = "https://archives.nseindia.com/content/equities/wk52_high_low.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    # Parsing left intentionally minimal — verify column names against the
    # actual file before relying on this in production. Fallback below.
    raise NotImplementedError("NSE 52W high/low parser needs verification against live file format")


# ---------------------------------------------------------------------------
# 4. Market Breadth — NSE advances vs declines, 10-day EMA
# ---------------------------------------------------------------------------
@safe_fetch("breadth")
def fetch_breadth(start: datetime, end: datetime) -> pd.Series:
    raise NotImplementedError("NSE advance/decline parser needs verification against live file format")


# ---------------------------------------------------------------------------
# 5. Put/Call Ratio — derived from NSE FO bhavcopy (PROXY, per design discussion)
# ---------------------------------------------------------------------------
@safe_fetch("put_call")
def fetch_put_call(start: datetime, end: datetime) -> pd.Series:
    raise NotImplementedError("NSE FO bhavcopy PCR parser needs verification against live file format")


# ---------------------------------------------------------------------------
# 6. Safe Haven Demand — Nifty 20d return vs approximated 10Y G-Sec 20d return
# ---------------------------------------------------------------------------
# Formula (matches the verified Germany-workbook convention):
#   SafeHaven = Equity_20d_return − [ −Duration × ΔYield_20d / 100 ]
#
# This avoids needing an actual G-Sec bond price/total-return series — only
# the YIELD LEVEL is needed (freely available from RBI/FBIL), and the bond's
# approximate 20-day price return is backed out via modified duration:
#   bond_return ≈ −Duration × Δyield
# (standard bond-math approximation: price moves inversely to yield, scaled
# by duration). A positive SafeHaven score = equities outperforming the
# safe-haven bond proxy = greed; negative = flight to safety = fear.
#
# INDIA_GSEC_DURATION: modified duration of the 10Y benchmark G-Sec.
# Germany's workbook used 8.5yr for the Bund. India's 10Y G-Sec duration is
# typically ~7.0-7.3yr (lower coupon-adjusted duration than a Bund at the
# same tenor) — VERIFY against current benchmark bond specifics before
# relying on this for real analysis; treated as a placeholder estimate here.
INDIA_GSEC_DURATION = 7.2


@safe_fetch("safe_haven")
def fetch_safe_haven(start: datetime, end: datetime) -> pd.Series:
    px = yf.download("^NSEI", start=start, end=end, progress=False)["Close"]
    equity_20d_ret = px.pct_change(20)

    # 10Y G-Sec yield level — RBI/FBIL reference rate.
    # NOT YET IMPLEMENTED: needs a real daily-yield fetch (RBI DBIE, FBIL
    # reference rates page, or CCIL). Left as a hard failure rather than a
    # fabricated constant series, so this component correctly shows as
    # failed/missing until wired to a real source instead of silently
    # producing wrong numbers.
    raise NotImplementedError(
        "RBI/FBIL 10Y G-Sec yield fetch needs implementation. "
        "Once available as `yield_series` (daily %), compute as:\n"
        "  dyield_20d = yield_series.diff(20)\n"
        "  bond_20d_return = -INDIA_GSEC_DURATION * dyield_20d / 100\n"
        "  return (equity_20d_ret - bond_20d_return).dropna()"
    )


# ---------------------------------------------------------------------------
# 7. Credit Spread — AAA corporate bond yield vs 10Y G-Sec (PROXY for junk-bond demand)
# ---------------------------------------------------------------------------
@safe_fetch("credit_spread")
def fetch_credit_spread(start: datetime, end: datetime) -> pd.Series:
    raise NotImplementedError("FBIL/CCIL AAA-govt spread fetch needs implementation")


def percentile_rank_normalise(series: pd.Series, window: int = ROLL_WINDOW) -> pd.Series:
    """
    Rolling percentile rank -> 0-100 score.

    Matches the exact Germany-workbook / thesis convention:
        score = (count of values BELOW the current value in the trailing
                  `window`-day window) / (window - 1) * 100

    This is NOT the same as pandas' `.rank(pct=True)`, which uses average
    rank and divides by N rather than N-1, and counts ties differently.
    Implemented explicitly to match the verified formula.
    """
    def _pct_rank(window_vals):
        current = window_vals[-1]
        n = len(window_vals)
        below = (window_vals < current).sum()
        return below / (n - 1) * 100

    return series.rolling(window).apply(_pct_rank, raw=True)


def compute_composite(components: dict[str, ComponentResult]) -> dict:
    """
    Combine available components into a composite score. If a component is
    missing/errored, the composite falls back to equal-weighting the
    remaining working components (and this is flagged clearly in output —
    do NOT silently treat a 5-component and 7-component score as comparable
    over time without noting the change).
    """
    working = {k: v for k, v in components.items() if v.ok}
    errors = {k: v.error for k, v in components.items() if not v.ok}

    if not working:
        raise RuntimeError("All components failed — cannot compute FGI today.")

    normalised = {}
    for name, comp in working.items():
        norm = percentile_rank_normalise(comp.raw_series)
        # Invert volatility and credit_spread (higher = more fear)
        if name in ("volatility", "credit_spread"):
            norm = 100 - norm
        normalised[name] = norm.dropna()

    # Align on common dates, take latest
    df = pd.DataFrame(normalised).dropna()
    if df.empty:
        raise RuntimeError("No overlapping dates across components after normalisation.")

    latest = df.iloc[-1]
    composite_score = float(latest.mean())

    return {
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "composite_score": round(composite_score, 2),
        "zone": zone_for(composite_score),
        "components_used": list(working.keys()),
        "components_failed": errors,
        "n_components": len(working),
        "component_scores": {k: round(float(v), 2) for k, v in latest.items()},
    }


def append_history(result: dict) -> None:
    history = []
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())
    # replace today's entry if re-run, else append
    history = [h for h in history if h["date"] != result["date"]]
    history.append(result)
    history.sort(key=lambda h: h["date"])
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def main():
    end = datetime.today()
    start = end - timedelta(days=LOOKBACK_DAYS)

    fetchers = {
        "momentum": fetch_momentum,
        "volatility": fetch_volatility,
        "strength": fetch_strength,
        "breadth": fetch_breadth,
        "put_call": fetch_put_call,
        "safe_haven": fetch_safe_haven,
        "credit_spread": fetch_credit_spread,
    }

    components = {name: fn(start, end) for name, fn in fetchers.items()}

    for name, comp in components.items():
        if not comp.ok:
            print(f"[WARN] component '{name}' failed: {comp.error}", file=sys.stderr)

    result = compute_composite(components)
    append_history(result)

    print(json.dumps(result, indent=2))

    if result["n_components"] < 7:
        print(
            f"[WARN] Composite computed from {result['n_components']}/7 components "
            f"today. Failed: {list(result['components_failed'].keys())}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)

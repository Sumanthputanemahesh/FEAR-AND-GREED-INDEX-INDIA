# India Fear & Greed Index — Live

A daily-updating India Fear & Greed Index, built entirely from free public
data and run automatically on GitHub Actions. Same construction methodology
and repo structure as the companion
[UK Fear & Greed Index](https://github.com/Sumanthputanemahesh/FEAR-AND-GREED-INDEX-UK)
(itself adapted from an MSc Finance thesis pipeline, Cranfield University,
2025–26), applied to Indian markets.

**Live dashboard:** enable GitHub Pages (Settings → Pages → Source: GitHub
Actions) and it will publish at
`https://sumanthputanemahesh.github.io/FEAR-AND-GREED-INDEX-INDIA/`.

## How it works

1. `.github/workflows/update-fgi.yml` runs on a cron schedule (weekdays,
   12:30 UTC = 18:00 IST, after NSE close and the F&O bhavcopy publish):
   - `scripts/build_india_fgi.py` pulls fresh data and recomputes the index
   - `scripts/publish_latest.py` derives `docs/data/latest.json` for the
     dashboard
   - both commit their output back to the repo and redeploy `docs/` to
     GitHub Pages
2. `docs/index.html` is a static dashboard (Chart.js), styled after CNN
   Business's Fear & Greed page — semicircular gauge with a needle, a
   now/previous-close/1-week/1-month/1-year timeline, a per-component
   breakdown, and a dual history chart (zone-shaded FGI vs. Nifty 50 price
   rebased to 100) — identical layout to the UK site.

To trigger a run manually: **Actions → Update India Fear & Greed Index →
Run workflow**. To run locally:

```bash
pip install -r requirements.txt
python scripts/build_india_fgi.py     # first run backfills ~780 days of
                                        # Put/Call data one HTTP request per
                                        # day — allow 20-40 minutes; later
                                        # runs only fetch missing days
python scripts/publish_latest.py
```

## Methodology

Same normalization as the UK index: each raw component is converted to a
0–100 score via a **rolling 252-trading-day percentile rank**
(`(count of values below current) / (window − 1) × 100`), fear-positive
components are inverted, and the composite is the simple mean of whichever
components are available.

| Zone | Score |
|---|---|
| Extreme Fear | 0–25 |
| Fear | 26–45 |
| Neutral | 46–55 |
| Greed | 56–75 |
| Extreme Greed | 76–100 |

### Components

| # | Component | Source | Status |
|---|---|---|---|
| 1 | Market Momentum — Nifty 50 vs its 125-day MA | Yahoo Finance `^NSEI` | working |
| 2 | Volatility — India VIX *(inverted)* | Yahoo Finance `^INDIAVIX` | working |
| 3 | Price Strength — 52-week highs vs lows | computed from 48 Nifty 50 constituents on Yahoo Finance | working |
| 4 | Market Breadth — advances vs declines, 10-day EWM | computed from the same 48 constituents | working |
| 5 | Safe Haven Demand — Nifty 20d return vs gilt ETF 20d return | Yahoo Finance `LTGILTBEES.NS` | working |
| 6 | Put/Call Ratio *(inverted)* | NSE F&O UDiFF bhavcopy, parsed directly | working, shorter history (from mid-2024) |
| 7 | Credit Spread *(inverted)* | — | **omitted** — no free daily India HY-IG spread series exists |

Unlike the UK build, **Put/Call actually works here** — NSE's bhavcopy is
fetched and parsed directly (no CBOE-style network block), so it's a
genuine India-specific options signal, not a US proxy.

Credit Spread is omitted rather than proxied. This is a documented
market-structure limitation (India has no liquid, free, daily high-yield vs
investment-grade spread series), not a coding gap.

### Two published composites

Put/Call can only reach back to mid-2024 (NSE's UDiFF bhavcopy format
didn't exist before then, and 252 days are then consumed by the
normalisation warm-up). Requiring it would truncate the whole index to
about a year, so both are published:

| | CORE | EXTENDED |
|---|---|---|
| Components | 5 (no Put/Call) | 6 (adds Put/Call) |
| History | ~1,475 days, from June 2020 | ~275 days, from June 2025 |
| Used for | the history chart (longest run) | today's headline reading + component breakdown |

They correlate strongly (~0.96 historically), so CORE loses very little —
but they are **not interchangeable**: a CORE score and an EXTENDED score
for the same day come from different component sets and will differ,
typically by several points.

## Known issues and caveats

- **NSE may block the Put/Call fetch.** The script sends browser headers
  and primes cookies, but NSE is aggressive about scripted access, and
  behaviour can differ from a GitHub Actions runner's IP vs. a home
  connection. If every day fails, `docs/data/build_report.json` will say
  so, and the index simply builds without Put/Call (falling back toward
  CORE) rather than crashing.
- **Nifty 50 membership changes roughly twice a year.** The constituent
  list in `scripts/build_india_fgi.py` is a snapshot and will drift over
  time. Check `docs/data/build_report.json` → `strength_and_breadth`
  periodically for tickers that start failing.
- **No forward-return predictability found in this sample.** The index
  level shows essentially no correlation with subsequent Nifty returns
  (r ≈ −0.004 / −0.023 / −0.008 at 1 week, 1 month, 3 months) — a genuine
  negative result, and different from the UK index's contrarian signal. The
  cause isn't established; the ~6-year sample is short and the component
  set is smaller. Don't assume a contrarian trading signal exists here.
- **Safe Haven uses a gilt ETF, not a yield series.** `LTGILTBEES.NS` price
  returns stand in for the bond leg — conceptually closer to a true bond
  return than the UK build's duration-scaled yield approximation, but it
  ties the component's earliest possible date to the ETF's listing (2018).

## Disclaimer

For research and information only. Not investment advice. Nothing here is
a recommendation to buy or sell any security.

## Files

```
scripts/build_india_fgi.py    — data fetch, signal computation, normalization, CORE + EXTENDED composites
scripts/publish_latest.py     — derives docs/data/latest.json for the dashboard
scripts/legacy_*.py.bak        — earlier draft pipelines, superseded, kept for reference only, not run by the workflow
docs/index.html                 — the live dashboard (same design as the UK site)
docs/data/india_fgi_core.csv    — 5-component composite, long history
docs/data/india_fgi_extended.csv — 6-component composite, shorter history
docs/data/pcr_cache.csv          — cached Put/Call ratios (avoids re-downloading NSE bhavcopy files)
docs/data/build_report.json      — full run log: what worked, what failed, per component
.github/workflows/update-fgi.yml — daily cron: rebuild + publish + commit + redeploy Pages
```

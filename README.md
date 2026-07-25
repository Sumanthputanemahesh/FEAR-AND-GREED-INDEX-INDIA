# India Fear & Greed Index

A seven-component market sentiment gauge for Indian equities, built entirely
from free public data and refreshed automatically every weekday.

**Live site:** `https://<your-username>.github.io/<repo-name>/`

---

## What it measures

| # | Component | Source | Status |
|---|-----------|--------|--------|
| 1 | Market momentum — Nifty 50 vs its 125-day MA | Yahoo Finance `^NSEI` | working |
| 2 | Volatility — India VIX *(inverted)* | Yahoo Finance `^INDIAVIX` | working |
| 3 | Price strength — 52-week highs vs lows | computed from 48 Nifty constituents | working |
| 4 | Market breadth — advances vs declines, 10-day EWM | computed from 48 Nifty constituents | working |
| 5 | Safe haven demand — Nifty 20d return vs gilt 20d return | Yahoo Finance `LTGILTBEES.NS` | working |
| 6 | Put/Call ratio — Nifty index options *(inverted)* | NSE F&O UDiFF bhavcopy | working, short history |
| 7 | Credit spread — HY vs IG *(inverted)* | — | **omitted** |

Components 3 and 4 are derived from individual constituent prices rather than
scraped from NSE's breadth reports. That removes two fragile dependencies.

Component 7 is omitted deliberately: India has no liquid, free, daily
high-yield versus investment-grade spread series. This is a market-structure
limitation, not a coding gap.

## Two published indices

Put/Call can only reach back to mid-2025, because NSE's UDiFF bhavcopy format
did not exist before July 2024 and 252 trading days are then consumed by the
normalisation warm-up. Requiring it would truncate the whole index to about a
year, so both versions are published:

| | Core | Extended |
|---|------|----------|
| Components | 5 | 6 |
| History | ~1,475 days, from June 2020 | ~275 days, from June 2025 |
| Use for | analysis, backtesting, regime work | fullest read on today |

They correlate at **0.96**, so Core loses very little. They are **not**
interchangeable — a Core score and an Extended score for the same day are
computed from different component sets and will differ (typically by ~7 points).

## Methodology

Each component is scored 0–100 by its **rolling 252-day percentile rank**:

```
score = (count of values below current in trailing 252 days) / 251 * 100
```

Volatility, Put/Call and Credit Spread are inverted (`100 - score`) so that a
high reading always means greed. The composite is the equal-weighted mean of
all available component scores, and a day is only scored if every component in
that version is present.

Zone bands (upper-inclusive, matching the UK and Germany index conventions):

| Score | Zone |
|-------|------|
| 0–25 | Extreme Fear |
| 26–45 | Fear |
| 46–55 | Neutral |
| 56–75 | Greed |
| 76–100 | Extreme Greed |

---

## Setup

### 1. Create the repository

```bash
cd ~/Desktop/QUANT
git init
git add .
git commit -m "India Fear & Greed Index"
```

Create a new repository on GitHub, then:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

### 2. Allow the Action to commit data back

**Settings → Actions → General → Workflow permissions** →
select **Read and write permissions** → Save.

Without this the daily run builds fine but cannot push the refreshed data.

### 3. Turn on GitHub Pages

**Settings → Pages → Source: Deploy from a branch** →
branch `main`, folder `/ (root)` → Save.

Your site appears at `https://<your-username>.github.io/<repo-name>/`
within a minute or two.

### 4. Run it once by hand

**Actions → Update India Fear & Greed Index → Run workflow.**

The first run backfills roughly 500 days of Put/Call data one file at a time,
so allow 20–40 minutes. Later runs only fetch missing days and finish in a few
minutes.

---

## Running locally

```bash
pip3 install yfinance pandas numpy requests
python3 build_india_fgi.py
```

Outputs land in `data/`:

| File | Contents |
|------|----------|
| `india_fgi_core.csv` | 5-component index, full history |
| `india_fgi_extended.csv` | 6-component index, short history |
| `india_fgi_history.json` | both series, consumed by the website |
| `india_fgi_components_raw.csv` | raw component values before normalisation |
| `pcr_cache.csv` | cached Put/Call ratios, avoids re-downloading |
| `build_report.json` | what worked, what failed, and why |

To preview the site locally:

```bash
python3 -m http.server 8000
# open http://localhost:8000
```

---

## Known issues and caveats

**NSE may block the Put/Call fetch.** The script sends browser headers and
primes cookies, but NSE is aggressive about scripted access and behaviour can
differ from a GitHub runner's IP. If every day fails, `build_report.json` will
say so and the index simply builds without Put/Call rather than crashing.

**Nifty 50 membership changes twice a year.** The constituent list in
`build_india_fgi.py` is a snapshot and will drift. Two tickers
(`LTIM.NS`, `TATAMOTORS.NS`) already 404 on Yahoo Finance and are excluded, so
breadth and strength are computed from 48 names rather than 50.

**No forward-return predictability was found.** In this sample the index level
shows essentially no correlation with subsequent Nifty returns
(r ≈ −0.004 / −0.023 / −0.008 at 1 week, 1 month and 3 months). This is a real
negative result and differs from the equivalent UK index. The cause is not
established — the ~6-year sample is short, the component set is smaller, and
Indian sentiment may simply behave differently. Do not assume a contrarian
signal exists here.

**Safe haven uses a gilt ETF, not a yield series.** `LTGILTBEES.NS` price
returns stand in for the bond leg. This is conceptually closer to a true bond
return than the duration approximation used in the UK and Germany builds, but
it does tie the component's start date to the ETF's listing (2018).

---

## Disclaimer

For research and information only. Not investment advice. Nothing here is a
recommendation to buy or sell any security.

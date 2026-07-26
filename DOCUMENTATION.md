# India Fear & Greed Index — System Documentation

This document explains, end to end, what this project is, how it runs live,
where every piece of data comes from, and how to maintain it. It's the
India-market sibling of the
[UK Fear & Greed Index](https://github.com/Sumanthputanemahesh/FEAR-AND-GREED-INDEX-UK)
— same pipeline structure, same dashboard design, same daily-Actions
mechanism — adapted for Nifty 50 / NSE data. Where something is genuinely
different between the two (and several things are), it's called out
explicitly rather than assumed.

---

## 1. What this is

A daily-updating **India Fear & Greed Index** — a 0–100 sentiment gauge for
Indian equities, built the same way CNN Business's Fear & Greed Index is
built for the US, and the same way the companion UK index is built for the
FTSE. Several independent market signals, each converted to a percentile
score, averaged into one composite. Runs entirely on **free data** and
**free infrastructure** (GitHub Actions + GitHub Pages).

**Live site:** `https://sumanthputanemahesh.github.io/FEAR-AND-GREED-INDEX-INDIA/`
(once GitHub Pages is enabled — see §6).

**Repo:** `https://github.com/Sumanthputanemahesh/FEAR-AND-GREED-INDEX-INDIA`

---

## 2. How the daily update works — the full loop

```
GitHub Actions cron (weekdays, 12:30 UTC = 18:00 IST)
        │
        ▼
scripts/build_india_fgi.py   — pulls fresh data, computes the index
        │
        ▼
scripts/publish_latest.py    — derives docs/data/latest.json for the dashboard
        │
        ▼
git commit + push            — new data lands in docs/data/ on the main branch
        │
        ▼
GitHub Pages redeploy        — docs/ is republished automatically
        │
        ▼
docs/index.html              — static page, fetches the JSON/CSV client-side
```

Identical mechanism to the UK repo: no server runs continuously, all
compute happens once a day inside a GitHub Actions runner, and the site
itself is static files served by GitHub Pages.

### Trigger

```yaml
on:
  schedule:
    - cron: "30 12 * * 1-5"   # 12:30 UTC, Monday-Friday = 18:00 IST
  workflow_dispatch: {}
```

**Why 12:30 UTC and not the UK's 18:00 UTC:** the NSE closes at 15:30 IST,
which is 10:00 UTC. 12:30 UTC gives 2.5 hours of buffer — later than the
close itself, but also comfortably after NSE typically publishes the day's
F&O bhavcopy file (needed for the Put/Call component), which tends to lag
the equity close by an hour or more.

---

## 3. Where every piece of data comes from (exact sources)

| Component | Live source | Exact identifier(s) | Fetched via |
|---|---|---|---|
| **Momentum** | Yahoo Finance | `^NSEI` (Nifty 50 index) | `yfinance` |
| **Volatility** | Yahoo Finance | `^INDIAVIX` | `yfinance` |
| **Strength** | Yahoo Finance | 48 Nifty 50 constituent tickers (see §3.1) | `yfinance`, bulk download |
| **Breadth** | Yahoo Finance | Same 48 constituent tickers | `yfinance` |
| **Safe Haven** | Yahoo Finance | `^NSEI` + a gilt ETF, tried in order: `LTGILTBEES.NS` → `GILT5YBEES.NS` → `EBBETF0433.NS` | `yfinance` |
| **Put/Call** | NSE (National Stock Exchange of India) | F&O UDiFF bhavcopy ZIP, one file per trading day | `requests`, direct ZIP/CSV parse — see §3.2 |
| **Credit Spread** | — | — | **not implemented** — no free daily India HY-IG spread series exists (§3.3) |

### 3.1 Nifty 50 constituent list (Strength & Breadth)

A hardcoded snapshot of 48 NSE tickers lives in `NIFTY50_CONSTITUENTS`
inside `scripts/build_india_fgi.py` (2 of the true 50 — `LTIM.NS` and
`TATAMOTORS.NS` — already 404 on Yahoo Finance and are excluded). Every run
bulk-downloads daily closes for all of them and computes:
- **Breadth**: net advances minus declines across the 48 names, 10-day EWM
- **Strength**: fraction of the 48 currently at a rolling 252-day high vs.
  low (0.5 when neither)

Nifty 50 membership changes roughly twice a year. Not automatically
refreshed — see §7.

### 3.2 Put/Call — how it actually works here (unlike the UK build)

The UK index's Put/Call component is a compromise: CBOE's CSV is blocked
from cloud IPs, so it falls back to a US-market proxy via CNN's API. India's
is **not a proxy** — it's the genuine NSE index-options Put/Call ratio,
computed directly:

1. For each trading day, fetch NSE's **F&O UDiFF bhavcopy** ZIP:
   `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip`
2. Unzip and parse the CSV, filtering to `TckrSymb == 'NIFTY'` and
   `FinInstrmTp == 'IDO'` (index options, as opposed to stock options or
   futures).
3. Sum open interest (`OpnIntrst`) separately for `OptnTp == 'PE'` (puts)
   and `OptnTp == 'CE'` (calls). PCR = puts / calls.
4. Cache every day's result in `docs/data/pcr_cache.csv` — subsequent runs
   only fetch missing dates, not the whole history again.

This is one HTTP request **per trading day**, so the very first run
backfills ~780 days one file at a time and can take 20–40 minutes. Every
run after that only fetches new days and finishes in a few minutes.

**Why it's not always available:** NSE is aggressive about blocking
scripted access, and behaviour can differ between a home connection and a
GitHub Actions runner's IP. The fetch sends browser-like headers and primes
a session cookie first, but this is inherently less stable than a public
API. If a day's fetch fails, it's simply marked unavailable
(`days_unavailable` in `build_report.json`) rather than crashing the run.

**Why its history is short:** NSE's UDiFF bhavcopy format didn't exist
before **July 2024**. Combined with the 252-day rolling-percentile
warm-up, Put/Call's first valid score can't appear before roughly mid-2025
— which is why it lives only in the EXTENDED composite, not CORE (§4).

### 3.3 Credit Spread — why it's omitted, not proxied

The UK build proxies Credit Spread with a GBP/EUR HY-vs-IG bond ETF return
spread. No equivalent free, liquid, daily India HY-vs-IG series exists —
India's corporate bond market is far less securitised into tradeable ETFs
across credit tiers than the UK/US markets. Rather than force a weak or
misleading proxy, this component is simply **not implemented**, and the
composite is computed as an equal-weighted mean over 6 components instead
of 7. This is documented in `build_report.json` under `credit_spread` with
`status: "NOT_IMPLEMENTED"` and the reasoning, every single run.

Three options were considered and rejected for now (see the script's
comments): dropping it permanently (current choice), scraping an
FBIL/CCIL AAA-vs-GSec spread, or proxying with smallcap-vs-largecap
relative performance (rejected — that's an equity signal standing in for a
credit signal, which risks double-counting momentum rather than adding
independent information).

---

## 4. Methodology (exact math)

Identical formula to the UK index:

```
score(t) = (count of values in the trailing 252-day window strictly below value(t))
           / (252 − 1) × 100
```

Volatility, Put/Call, and (if ever implemented) Credit Spread are inverted
(`100 − score`) since a higher raw value means more fear for these three.
The composite is the simple mean of whichever components are available
that day; a day is only scored if every component **in that composite
version** is present (no partial-credit averaging within CORE or EXTENDED).

| Score | Zone |
|---|---|
| 0–25 | Extreme Fear |
| 26–45 | Fear |
| 46–55 | Neutral |
| 56–75 | Greed |
| 76–100 | Extreme Greed |

### CORE vs. EXTENDED — and why the dashboard prefers EXTENDED here (unlike UK)

- **`india_fgi_core.csv`** — 5 components (Momentum, Volatility, Strength,
  Breadth, Safe Haven). ~1,475 days, from **June 2020**. Used for the
  history chart, since it has by far the longest run.
- **`india_fgi_extended.csv`** — CORE + Put/Call (6 components). ~275 days,
  from **June 2025** (bounded by Put/Call's short NSE-format history, see
  §3.2).

**The dashboard's headline gauge uses EXTENDED** — the opposite default
from a naive "always use the longest series" rule, and worth explaining
why: EXTENDED already has ~275 days of history here (vs. the UK sibling's
EXTENDED, which only had ~126 days when last checked, because the UK's
Put/Call proxy is bottlenecked by a 120-day window on top of CNN's ~1-year
API history). India's Put/Call is a **direct** NSE computation with a full
252-day rolling window, not a shortened one, so EXTENDED here is both more
complete *and* has a reasonably long history — there's no real tradeoff to
justify defaulting to CORE for the headline number the way there might be
elsewhere.

`scripts/publish_latest.py` still falls back to CORE automatically if
EXTENDED ever has no data (e.g. NSE blocks every Put/Call fetch for an
extended period).

---

## 5. Relationship to the UK index — what's the same, what's different

| | UK | India |
|---|---|---|
| Components | 7 (all populated, one via a US-market proxy) | 6 (Credit Spread genuinely omitted, not proxied) |
| Put/Call source | CNN API (US proxy — CBOE blocked) | **NSE bhavcopy directly (native, not a proxy)** |
| Put/Call rolling window | 120 days (shortened — CNN history too short for 252) | 252 days (standard — full window used) |
| Dashboard headline uses | EXTENDED (7-comp, ~126 days) | EXTENDED (6-comp, ~275 days) |
| History chart uses | CORE (5-comp, ~4,400+ days) | CORE (5-comp, ~1,475 days) |
| Cron schedule | 18:00 UTC (after LSE close) | 12:30 UTC = 18:00 IST (after NSE close + bhavcopy publish) |
| Predictive relationship to forward returns | Contrarian signal found (thesis result) | **No relationship found** — r ≈ 0 at 1wk/1mo/3mo (§ README "Known issues") |

The last row is the most important difference to keep in mind: the UK
build's whole reason for existing traces back to a thesis finding that
sentiment-beta decile portfolios earn a significant spread. **No equivalent
result has been established for India** — this index is published as a
sentiment gauge, not as a validated trading signal, and the README says so
explicitly.

---

## 6. One-time setup checklist (things you do once in the GitHub UI)

1. **Enable GitHub Pages**: repo → **Settings → Pages → Source: GitHub
   Actions**.
2. **Confirm Actions permissions**: repo → **Settings → Actions → General →
   Workflow permissions → Read and write permissions**.
3. No secrets, no API keys, no paid accounts required anywhere in this
   pipeline.

---

## 7. Ongoing maintenance

- **Nifty 50 constituent list drifts.** Check `docs/data/build_report.json`
  → `strength_and_breadth.missing` periodically; update
  `NIFTY50_CONSTITUENTS` in `scripts/build_india_fgi.py` if more tickers
  start failing.
- **NSE may tighten its blocking of scripted access at any time.** If
  `build_report.json` → `put_call.status` starts showing `FAILED` for
  extended periods, Put/Call (and therefore EXTENDED) will stop updating
  — the dashboard will fall back to CORE automatically, so the site keeps
  working, just with one fewer component and a longer, lower-resolution
  headline series.
- **Gilt ETF candidates could stop trading.** `LTGILTBEES.NS` is the
  primary Safe Haven bond leg; `GILT5YBEES.NS` and `EBBETF0433.NS` are
  automatic fallbacks, logged in `build_report.json` if the primary ever
  fails.
- **Yahoo Finance is an unofficial data source** (`yfinance` scrapes
  Yahoo's public endpoints). Reliable in practice but has no formal SLA.

---

## 8. File reference

```
scripts/build_india_fgi.py     — fetches data, computes all 6 components, writes CORE + EXTENDED composites
scripts/publish_latest.py      — derives docs/data/latest.json for the dashboard
scripts/legacy_*.py.bak         — earlier draft pipelines (india_fgi_pipeline.py, yfinance_data_puller.py),
                                   superseded by build_india_fgi.py, kept for reference, not run by the workflow

docs/index.html                  — the live dashboard (identical design to the UK site)
docs/data/india_fgi_core.csv     — 5-component composite, long history (2020→today)
docs/data/india_fgi_extended.csv — 6-component composite, shorter history (bounded by Put/Call)
docs/data/india_fgi_components_raw.csv — every raw (pre-normalisation) signal
docs/data/india_fgi_history.json — full history in JSON form (core + extended)
docs/data/latest.json            — today's snapshot: score, zone, components, timeline
docs/data/build_report.json      — full run log: what worked, what failed, per component
docs/data/pcr_cache.csv          — cached Put/Call ratios (avoids re-fetching NSE bhavcopy files)
docs/data/yf_raw/                — raw pulled series (Nifty price, constituent closes, India VIX)

.github/workflows/update-fgi.yml — the cron job: build → publish → commit → redeploy Pages
requirements.txt                  — Python dependencies (yfinance, pandas, numpy, requests)
README.md                         — shorter public-facing overview
```

---

## 9. Running it yourself, locally

```bash
git clone https://github.com/Sumanthputanemahesh/FEAR-AND-GREED-INDEX-INDIA.git
cd FEAR-AND-GREED-INDEX-INDIA
pip install -r requirements.txt

python scripts/build_india_fgi.py   # first run: 20-40 min (Put/Call backfill)
python scripts/publish_latest.py

cd docs && python3 -m http.server 8000
# open http://localhost:8000
```

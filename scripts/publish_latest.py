"""
Derives docs/data/latest.json (consumed by docs/index.html) from whichever
of india_fgi_core.csv / india_fgi_extended.csv build_india_fgi.py produced.

EXTENDED (includes Put/Call) is preferred over CORE for today's headline
reading, but ONLY when it's at least as current — if NSE's bhavcopy fetch
ever lags or fails for a day, EXTENDED would otherwise leave the site
showing a stale reading while CORE (and the raw price data) is fully up to
date. Whichever composite has the more recent date wins; ties go to
EXTENDED for the extra component. Mirrors scripts/publish_latest.py in the
UK sibling repo.

Run after build_india_fgi.py:  python3 scripts/publish_latest.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "docs" / "data"
CORE_CSV = DATA_DIR / "india_fgi_core.csv"
EXT_CSV = DATA_DIR / "india_fgi_extended.csv"
NIFTY_RAW = DATA_DIR / "yf_raw" / "nifty50.csv"
LATEST_JSON = DATA_DIR / "latest.json"


def load(path):
    if not path.exists():
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df if not df.empty else None


def main():
    core = load(CORE_CSV)
    ext = load(EXT_CSV)

    if core is None and ext is None:
        raise RuntimeError("Neither india_fgi_core.csv nor india_fgi_extended.csv has data — "
                            "run scripts/build_india_fgi.py first.")

    # Pick whichever composite is more current; EXTENDED wins ties since it
    # has the extra component. Stops a lagging Put/Call fetch from silently
    # making the site's headline reading stale for days while CORE (and the
    # underlying price data) is actually fully up to date.
    if ext is not None and (core is None or ext.index[-1] >= core.index[-1]):
        source_df, source_label = ext, "extended"
    else:
        source_df, source_label = core, "core"

    # History chart always uses the longest available series (CORE).
    history_df = core if core is not None else ext

    latest_row = source_df.iloc[-1]
    comp_cols = [c for c in source_df.columns if c not in ("composite", "zone")]

    nifty_close = None
    if NIFTY_RAW.exists():
        px = pd.read_csv(NIFTY_RAW, index_col=0, parse_dates=True).iloc[:, 0]
        px = px[px.index <= source_df.index[-1]]
        if not px.empty:
            nifty_close = float(px.iloc[-1])

    def score_on_or_before(df, target_date):
        sub = df[df.index <= target_date]
        return None if sub.empty else round(float(sub.iloc[-1]["composite"]), 2)

    last_date = source_df.index[-1]
    timeline = {
        "now": round(float(latest_row["composite"]), 2),
        "prev_close": score_on_or_before(source_df, last_date - pd.Timedelta(days=1)),
        "week_ago": score_on_or_before(source_df, last_date - pd.Timedelta(days=7)),
        "month_ago": score_on_or_before(source_df, last_date - pd.Timedelta(days=30)),
        "year_ago": score_on_or_before(history_df, last_date - pd.Timedelta(days=365)),
    }

    payload = {
        "date": last_date.strftime("%Y-%m-%d"),
        "fgi": round(float(latest_row["composite"]), 2),
        "zone": latest_row["zone"],
        "source": source_label,
        "n_components": len(comp_cols),
        "nifty_close": nifty_close,
        "components": {c: round(float(latest_row[c]), 1) for c in comp_cols},
        "timeline": timeline,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history_days": len(source_df),
        "core_history_days": len(core) if core is not None else 0,
    }
    LATEST_JSON.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {LATEST_JSON} — {payload['fgi']} ({payload['zone']}) "
          f"as of {payload['date']}, source={source_label}")


if __name__ == "__main__":
    main()

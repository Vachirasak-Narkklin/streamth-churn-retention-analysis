"""
Collect public app reviews (Thailand store) for competitive pain-point analysis.

Sources
- Google Play  (via the `google-play-scraper` library)
- App Store    (Apple's public customer-reviews RSS feed, max ~500 most recent per app)

Privacy: reviewer names / avatars are NOT saved. Output is for private analysis;
publish only aggregated results, never the raw review text.

Run on your own computer (needs internet):
    pip install google-play-scraper pandas
    python src/collect_reviews.py                 # default 1,000 Play reviews per app
    python src/collect_reviews.py --play-count 2000

Output: data/external/reviews_raw.csv
        data/external/apps_resolved.csv   (which store listing was used per app)
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
from google_play_scraper import Sort, app as play_app, reviews

OUT = Path(__file__).resolve().parents[1] / "data" / "external"
COUNTRY, LANG = "th", "th"

# Explicit MOBILE package names (store search returned TV apps / unrelated games).
# Several candidates per app: the first one that exists and returns reviews is used.
PLAY_IDS = {
    "Netflix": ["com.netflix.mediaclient"],
    "Prime Video": ["com.amazon.avod.thirdpartyclient"],
    "Disney+": ["com.disney.disneyplus", "in.startv.hotstar.dplus"],
    "iQIYI": ["com.iqiyi.i18n"],
    "WeTV": ["com.tencent.qqlivei18n"],
    "Viu": ["com.vuclip.viu"],
}
# App Store track IDs (resolved via the iTunes Search API, Thailand storefront)
APPSTORE_IDS = {
    "Netflix": "363590051",
    "Prime Video": "545519333",
    "Disney+": "1446075923",
    "iQIYI": "1461999674",
    "WeTV": "1441531611",
    "Viu": "1036095179",
}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# ------------------------------------------------------------- Google Play
def fetch_play(app_id: str, n: int) -> list[dict]:
    out, token = [], None
    while len(out) < n:
        batch, token = reviews(app_id, lang=LANG, country=COUNTRY, sort=Sort.NEWEST,
                               count=min(200, n - len(out)), continuation_token=token)
        if not batch:
            break
        out.extend(batch)
        if token is None:
            break
        time.sleep(0.5)
    return [{
        "review_id": r["reviewId"],
        "date": r["at"],
        "rating": r["score"],
        "title": None,
        "text": r["content"],
        "app_version": r.get("reviewCreatedVersion"),
        "thumbs_up": r.get("thumbsUpCount"),
    } for r in out]


def collect_play(app_name: str, n: int):
    for pkg in PLAY_IDS[app_name]:
        try:
            title = play_app(pkg, lang=LANG, country=COUNTRY)["title"]
        except Exception as e:                      # noqa: BLE001
            print(f"  google_play: {pkg} not available in TH ({type(e).__name__})")
            continue
        data = fetch_play(pkg, n)
        if data:
            return pkg, title, data
        print(f"  google_play: {pkg} returned 0 reviews, trying next candidate")
    return None


# --------------------------------------------------------------- App Store
APPSTORE_URLS = [
    "https://itunes.apple.com/{cc}/rss/customerreviews/page={p}/id={id}/sortby=mostrecent/json",
    "https://itunes.apple.com/{cc}/rss/customerreviews/page={p}/id={id}/json",
    "https://itunes.apple.com/rss/customerreviews/page={p}/id={id}/sortby=mostrecent/json?cc={cc}",
]


def parse_entries(payload: dict) -> list[dict]:
    entries = payload.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    return [e for e in entries if "im:rating" in e]   # first entry can be app metadata


def fetch_appstore(track_id: str) -> list[dict]:
    # find a URL pattern that works (Apple changes this feed from time to time)
    pattern, first = None, []
    for tpl in APPSTORE_URLS:
        url = tpl.format(cc=COUNTRY, p=1, id=track_id)
        try:
            payload = http_json(url)
        except urllib.error.HTTPError as e:
            print(f"    feed variant failed: HTTP {e.code}")
            continue
        except Exception as e:                      # noqa: BLE001
            print(f"    feed variant failed: {type(e).__name__}: {e}")
            continue
        first = parse_entries(payload)
        if first:
            pattern = tpl
            break
        keys = list(payload.get("feed", {}).keys())
        print(f"    feed variant returned no reviews (feed keys: {keys[:6]})")
    if not pattern:
        return []

    raw = list(first)
    for page in range(2, 11):                       # Apple serves at most 10 pages x 50
        try:
            entries = parse_entries(http_json(pattern.format(cc=COUNTRY, p=page, id=track_id)))
        except Exception:                           # noqa: BLE001
            break
        if not entries:
            break
        raw.extend(entries)
        time.sleep(0.5)
    return [{
        "review_id": e["id"]["label"],
        "date": e.get("updated", {}).get("label"),
        "rating": int(e["im:rating"]["label"]),
        "title": e.get("title", {}).get("label"),
        "text": e.get("content", {}).get("label"),
        "app_version": e.get("im:version", {}).get("label"),
        "thumbs_up": None,
    } for e in raw]


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--play-count", type=int, default=1000, help="Google Play reviews per app")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    rows, resolved = [], []
    for app_name in PLAY_IDS:
        print(f"\n== {app_name}")

        hit = collect_play(app_name, args.play_count)
        if hit:
            pkg, title, data = hit
            print(f"  google_play: {title} [{pkg}] -> {len(data)} reviews")
            resolved.append({"app": app_name, "source": "google_play", "store_id": pkg,
                             "listing_title": title, "n_reviews": len(data)})
            rows += [{"app": app_name, "source": "google_play", **r} for r in data]
        else:
            print("  google_play: no usable listing")

        tid = APPSTORE_IDS[app_name]
        data = fetch_appstore(tid)
        print(f"  app_store: [{tid}] -> {len(data)} reviews")
        resolved.append({"app": app_name, "source": "app_store", "store_id": tid,
                         "listing_title": None, "n_reviews": len(data)})
        rows += [{"app": app_name, "source": "app_store", **r} for r in data]

    df = pd.DataFrame(rows)
    pd.DataFrame(resolved).to_csv(OUT / "apps_resolved.csv", index=False)
    if df.empty:
        print("\nNo reviews collected - check your internet connection.")
        return
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df = df.drop_duplicates(["source", "review_id"])
    df.to_csv(OUT / "reviews_raw.csv", index=False, encoding="utf-8-sig")

    print("\nSummary (reviews per app & source):")
    print(df.groupby(["app", "source"]).size().unstack(fill_value=0))
    print("\nShare of 1-3 star reviews:", f"{(df.rating <= 3).mean():.0%}")
    print(f"\nSaved -> {OUT / 'reviews_raw.csv'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Daily price updater for Nichirin Battle Slash V6.

Price policy
------------
* Uses multiple public shop sources when they are reachable.
* A source that cannot be fetched is NOT treated as ¥0.
* The current market estimate is the median of the distinct source prices
  successfully fetched for that card on this run.
* Previous offers are retained as last-known values when a source temporarily
  fails, but today's history point uses ONLY prices fetched successfully today.
* One history snapshot is stored per calendar day. Existing history is never
  truncated, so the project can accumulate a multi-year price history.
* Mercari gets a direct search link, but this updater does not scrape Mercari.
  Mercari's current platform terms prohibit automated scraping/crawling, so an
  unavailable Mercari value remains unavailable rather than becoming ¥0.
"""

import html as html_module
import json
import re
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "catalog.json"
OUT = ROOT / "data" / "prices.json"
TODAY = time.strftime("%Y-%m-%d", time.gmtime())

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

CARD_ID = r"\b([APT]\d{2}-\d{3})\b"
CARD_ID_RE = re.compile(CARD_ID, re.I)

SOURCES = [
    {
        "name": "FullAhead",
        "shop": "FullAhead",
        "base": "https://pt-fullahead.com/shopbrand/kynbs/",
        "pages": range(1, 5),
        "kind": "fullahead",
    },
    {
        "name": "FullAhead Rakuten",
        "shop": "FullAhead Rakuten",
        "base": "https://item.rakuten.co.jp/fullahead/c/0000012537/",
        "pages": range(1, 3),
        "kind": "rakuten",
    },
    {
        "name": "TCG Library A01",
        "shop": "TCG Library",
        "base": (
            "https://tcg-library.com/collections/"
            "kimetsu-nichirin-battle-slash-a01"
        ),
        "pages": range(1, 3),
        "kind": "tcg",
    },
    {
        "name": "TCG Library all",
        "shop": "TCG Library",
        "base": (
            "https://tcg-library.com/collections/"
            "kimetsu-nichirin-battle-slash"
        ),
        "pages": range(1, 4),
        "kind": "tcg",
    },
    {
        "name": "カードショップ カリントウ A01",
        "shop": "カードショップ カリントウ",
        "base": "https://item.rakuten.co.jp/karintou10/c/0000006149/",
        "pages": range(1, 2),
        "kind": "rakuten",
    },
    {
        "name": "カードショップ カリントウ A02",
        "shop": "カードショップ カリントウ",
        "base": "https://item.rakuten.co.jp/karintou10/c/0000006150/",
        "pages": range(1, 2),
        "kind": "rakuten",
    },
]

SHOP_LINKS = {
    "FullAhead": "https://pt-fullahead.com/shopbrand/kynbs/",
    "FullAhead Rakuten": (
        "https://item.rakuten.co.jp/fullahead/c/0000012537/"
    ),
    "TCG Library": (
        "https://tcg-library.com/collections/"
        "kimetsu-nichirin-battle-slash"
    ),
    "カードショップ カリントウ": (
        "https://item.rakuten.co.jp/karintou10/c/0000006149/"
    ),
}


def get(url):
    """Fetch a page and decode it using its declared character encoding."""
    request = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "ja,en;q=0.8",
        },
    )

    with urlopen(request, timeout=30) as response:
        raw = response.read()
        charset = response.headers.get_content_charset()

    if not charset:
        head = raw[:10000].decode("latin-1", "ignore")
        match = re.search(
            r"""charset\s*=\s*["']?\s*([a-zA-Z0-9._-]+)""",
            head,
            re.I,
        )
        if match:
            charset = match.group(1)

    candidates = []
    if charset:
        candidates.append(charset)

    candidates.extend(["utf-8", "cp932", "shift_jis", "euc_jp"])

    tried = set()
    for encoding in candidates:
        if encoding.lower() in tried:
            continue
        tried.add(encoding.lower())
        try:
            return raw.decode(encoding, "strict")
        except (UnicodeDecodeError, LookupError):
            continue

    return raw.decode("utf-8", "replace")


def clean(html):
    """Convert HTML into readable text for ordinary shop listings."""
    html = re.sub(
        r"<script\b[\s\S]*?</script>",
        " ",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"<style\b[\s\S]*?</style>",
        " ",
        html,
        flags=re.I,
    )
    html = re.sub(r"<[^>]+>", " ", html)
    html = html_module.unescape(html)
    html = html.replace("\xa0", " ")
    return re.sub(r"\s+", " ", html).strip()


def valid_price(value):
    try:
        price = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None

    if 1 <= price <= 1_000_000:
        return price
    return None


def parse_shop(html):
    """
    Parse ordinary Japanese listings where the card ID precedes its price.

    Prefer a regular listing over a listing explicitly labelled キズ格安
    when both appear on the same page.
    """
    text = clean(html)
    out = {}
    damaged = {}

    pattern = re.compile(
        CARD_ID
        + r".{0,320}?"
        + r"(?:[¥￥]\s*)?([0-9][0-9,]*)\s*円",
        re.I | re.S,
    )

    for match in pattern.finditer(text):
        cid = match.group(1).upper()
        price = valid_price(match.group(2))
        if price is None:
            continue

        prefix = text[max(0, match.start() - 45):match.start()]
        is_damaged = "キズ格安" in prefix

        # Keep the first regular listing, but allow it to replace a
        # previously seen damaged-card listing.
        if cid not in out or (damaged.get(cid, True) and not is_damaged):
            out[cid] = price
            damaged[cid] = is_damaged

    return out


def parse_tcg_library(html):
    """
    Read TCG Library's embedded Shopify product records.

    Records contain a JPY price and a product title such as
    竈門炭治郎（A01-001）［UR］.
    """
    # Some product data is embedded as an escaped JSON string.
    normalized = re.sub(r"\\+\"", '"', html)
    normalized = normalized.replace(r"\/", "/")

    out = {}

    record_pattern = re.compile(
        r'"price"\s*:\s*\{\s*'
        r'"amount"\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*'
        r'"currencyCode"\s*:\s*"JPY"\s*'
        r'\}\s*,\s*"product"\s*:\s*\{(.*?)\}',
        re.S,
    )

    title_pattern = re.compile(r'"title"\s*:\s*"([^"]*)"')

    for match in record_pattern.finditer(normalized):
        price = valid_price(match.group(1))
        if price is None:
            continue

        product = match.group(2)
        title_match = title_pattern.search(product)
        if not title_match:
            continue

        title = html_module.unescape(title_match.group(1))
        id_match = CARD_ID_RE.search(title)
        if not id_match:
            continue

        cid = id_match.group(1).upper()

        # Preserve the first occurrence within a source page.
        out.setdefault(cid, price)

    return out


def parse_prices(html, kind):
    if kind == "tcg":
        return parse_tcg_library(html)
    if kind == "fullahead":
        return parse_shop(html)
    if kind == "rakuten":
        # Do not treat an access-denied/reference page as product data.
        if len(html) < 200 or "Reference #" in html:
            return {}
        return parse_shop(html)
    return {}


def source_url(src, cid):
    return src["base"]


def mercari_url(card):
    query = " ".join(
        value
        for value in (
            card["id"],
            card.get("name", ""),
            str(card.get("rarity", "")).replace("*", ""),
        )
        if value
    )
    return "https://jp.mercari.com/search?" + urlencode(
        {"keyword": query}
    )


def median(values):
    values = [
        int(value)
        for value in values
        if isinstance(value, (int, float)) and value > 0
    ]
    if not values:
        return None
    return int(round(statistics.median(values)))


def make_history_point(values):
    values = [
        int(value)
        for value in values
        if isinstance(value, (int, float)) and value > 0
    ]
    if not values:
        return None

    return {
        "date": TODAY,
        "market": median(values),
        "average": int(round(sum(values) / len(values))),
        "cheapest": min(values),
    }


def main():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    old = (
        json.loads(OUT.read_text(encoding="utf-8"))
        if OUT.exists()
        else {"cards": {}}
    )
    oldcards = old.get("cards", {})

    cards = {
        card["id"]: dict(oldcards.get(card["id"], {}))
        for card in catalog["cards"]
    }
    card_by_id = {card["id"]: card for card in catalog["cards"]}

    # Fresh offers collected during this run, one per actual shop.
    fetched = {cid: {} for cid in cards}
    successful_sources = set()
    page_results = {}

    for src in SOURCES:
        for page in src["pages"]:
            if src["kind"] == "fullahead":
                url = (
                    src["base"]
                    if page == 1
                    else f"{src['base']}page{page}/order/"
                )
            elif src["kind"] == "tcg":
                url = (
                    src["base"]
                    if page == 1
                    else f"{src['base']}?page={page}"
                )
            else:
                url = (
                    src["base"]
                    if page == 1
                    else f"{src['base']}?p={page}"
                )

            try:
                page_html = get(url)
                prices = parse_prices(page_html, src["kind"])
                page_results[(src["name"], page)] = prices

                print(
                    f"PRICE DEBUG | {src['name']} | page {page} "
                    f"| extracted {len(prices)} card prices"
                )

                if not prices:
                    title_match = re.search(
                        r"<title[^>]*>(.*?)</title>",
                        page_html,
                        re.I | re.S,
                    )
                    title = (
                        clean(title_match.group(1))
                        if title_match
                        else "(no title)"
                    )
                    print(
                        f"FETCH DEBUG | {src['name']} | page {page} "
                        f"| chars={len(page_html)} | title={title[:100]}"
                    )

                if prices:
                    successful_sources.add(src["name"])

                for cid, price in prices.items():
                    if cid not in cards:
                        continue

                    # Two category pages belonging to one shop are not
                    # counted as two independent price sources. Keep the
                    # first price collected from that shop for this card.
                    fetched[cid].setdefault(
                        src["shop"],
                        {
                            "shop": src["shop"],
                            "source": src["name"],
                            "price": price,
                            "url": source_url(src, cid),
                            "updated": TODAY,
                            "stale": False,
                        },
                    )

            except Exception as error:
                print("WARN", src["name"], page, repr(error))

            time.sleep(0.5)

    fresh_count = sum(len(offers) for offers in fetched.values())

    print("Fresh card/shop prices:", fresh_count)
    print("Sources with extracted prices:", ", ".join(sorted(successful_sources)))

    # Crucially, do not write a new file if no prices were collected.
    if fresh_count == 0:
        raise RuntimeError(
            "No fresh card prices were collected. "
            "Existing prices.json was not updated."
        )

    for cid, card in card_by_id.items():
        previous = cards[cid]
        today_offers = list(fetched[cid].values())

        old_offers = {
            offer.get("shop"): offer
            for offer in previous.get("offers", [])
            if (
                isinstance(offer, dict)
                and offer.get("shop")
                and valid_price(offer.get("price")) is not None
            )
        }

        combined = []
        fresh_shops = set()

        for offer in today_offers:
            combined.append(offer)
            fresh_shops.add(offer["shop"])

        for shop, offer in old_offers.items():
            if shop not in fresh_shops:
                stale = dict(offer)
                stale["stale"] = True
                combined.append(stale)

        combined.sort(
            key=lambda offer: (
                str(offer.get("shop", "")),
                float(offer.get("price", 0)),
            )
        )
        previous["offers"] = combined

        fresh_values = [
            offer["price"]
            for offer in today_offers
            if valid_price(offer.get("price")) is not None
        ]

        if fresh_values:
            # All displayed estimates use today's data only.
            previous["price"] = min(fresh_values)
            previous["marketValue"] = median(fresh_values)
            previous["average"] = int(
                round(sum(fresh_values) / len(fresh_values))
            )
            previous["updated"] = TODAY
            previous["freshSourceCount"] = len(fresh_values)
        else:
            # Preserve last-known estimates if this card had no fresh data.
            previous.setdefault("price", None)
            previous.setdefault("marketValue", None)
            previous.setdefault("average", None)
            previous["freshSourceCount"] = 0

        previous["shopLinks"] = [
            {"shop": name, "url": url}
            for name, url in SHOP_LINKS.items()
        ]
        previous["shopLinks"].append(
            {
                "shop": "メルカリ",
                "url": mercari_url(card),
            }
        )

        # Mercari is a manual reference link, not an automated price source.
        previous["mercari"] = {
            "status": "manual-search",
            "median": None,
            "samples": [],
            "url": mercari_url(card),
            "updated": TODAY,
        }

        history = previous.get("history")
        if not isinstance(history, list):
            history = []

        # Keep all earlier dates, replacing only today's snapshot on reruns.
        history = [
            point
            for point in history
            if isinstance(point, dict) and point.get("date") != TODAY
        ]

        point = make_history_point(fresh_values)
        if point is not None:
            history.append(point)

        history.sort(key=lambda point: str(point.get("date", "")))
        previous["history"] = history
        cards[cid] = previous

    output = {
        "updated": TODAY,
        "cards": cards,
        "version": 6,
        "v6": 2,
        "historyPolicy": (
            "One snapshot per date when fresh prices are collected. "
            "All existing history dates are retained. "
            "Missing dates are not invented."
        ),
        "pricePolicy": (
            "Current marketValue and average use fresh positive prices "
            "from distinct shops collected during the current run. "
            "Old offers are retained and marked stale."
        ),
        "sourcesFetchedToday": sorted(successful_sources),
        "mercariNote": (
            "Mercari is a direct search link only; its prices are not "
            "automatically scraped or treated as zero."
        ),
    }

    # Write only after the complete update has been built successfully.
    OUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        "Updated",
        OUT,
        "cards=",
        len(cards),
        "fresh_prices=",
        fresh_count,
        "sources=",
        ", ".join(sorted(successful_sources)),
    )


if __name__ == "__main__":
    main()

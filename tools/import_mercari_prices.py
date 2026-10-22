#!/usr/bin/env python3
"""Import manually collected Mercari observations into data/prices.json. No scraping/API calls."""
import csv, json, statistics
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "mercari_import.csv"
PRICES_PATH = ROOT / "data" / "prices.json"
CATALOG_PATH = ROOT / "data" / "catalog.json"
MAX_SAMPLES = 50

def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default

def median(samples):
    values = [int(s["price"]) for s in samples if isinstance(s.get("price"), (int, float)) and s["price"] > 0]
    return int(round(statistics.median(values))) if values else None

def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"Missing CSV: {CSV_PATH}")
    catalog = read_json(CATALOG_PATH, {"cards": []})
    valid_ids = {str(c.get("id", "")).upper() for c in catalog.get("cards", []) if c.get("id")}
    prices = read_json(PRICES_PATH, {"cards": {}})
    if not isinstance(prices.get("cards"), dict):
        prices["cards"] = {}
    today = date.today().isoformat()
    imported = skipped = 0

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"card_id", "status", "price", "url"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise SystemExit("CSV must contain card_id,status,price,url (plus optional observed_date,sold_date,notes)")
        for line_no, row in enumerate(reader, 2):
            cid = (row.get("card_id") or "").strip().upper()
            status = (row.get("status") or "").strip().lower()
            url = (row.get("url") or "").strip()
            try:
                price = int((row.get("price") or "").replace(",", "").replace("¥", "").replace("￥", "").strip())
            except ValueError:
                print(f"SKIP line {line_no}: invalid price"); skipped += 1; continue
            if cid not in valid_ids:
                print(f"SKIP line {line_no}: unknown card ID {cid!r}"); skipped += 1; continue
            if status not in {"active", "sold"}:
                print(f"SKIP line {line_no}: status must be active or sold"); skipped += 1; continue
            if not 1 <= price <= 1_000_000:
                print(f"SKIP line {line_no}: price out of range"); skipped += 1; continue
            if not url.startswith("https://jp.mercari.com/"):
                print(f"SKIP line {line_no}: URL must be a Mercari Japan HTTPS URL"); skipped += 1; continue
            observed = (row.get("observed_date") or "").strip() or today
            sold_date = (row.get("sold_date") or "").strip()
            try:
                date.fromisoformat(observed)
                if sold_date: date.fromisoformat(sold_date)
            except ValueError:
                print(f"SKIP line {line_no}: dates must be YYYY-MM-DD"); skipped += 1; continue

            card = prices["cards"].setdefault(cid, {})
            mercari = card.setdefault("mercari", {})
            bucket_name = "active" if status == "active" else "sold"
            bucket = mercari.setdefault(bucket_name, {"samples": [], "median": None, "sampleCount": 0})
            samples = bucket.setdefault("samples", [])
            if any(str(s.get("url", "")) == url and str(s.get("status", status)).lower() == status
                   and int(s.get("price", 0) or 0) == price for s in samples if isinstance(s, dict)):
                print(f"SKIP line {line_no}: duplicate"); skipped += 1; continue
            sample = {"price": price, "url": url, "observedDate": observed, "status": status,
                      "notes": (row.get("notes") or "").strip()}
            if sold_date: sample["soldDate"] = sold_date
            samples.append(sample)
            samples.sort(key=lambda s: (str(s.get("observedDate", "")), str(s.get("soldDate", ""))))
            bucket["samples"] = samples[-MAX_SAMPLES:]
            bucket["median"] = median(bucket["samples"])
            bucket["sampleCount"] = len(bucket["samples"])
            bucket["updated"] = today
            mercari["url"] = f"https://jp.mercari.com/search?keyword={cid}"
            mercari["status"] = "manual-import"
            imported += 1

    prices["updated"] = today
    prices["mercariImportPolicy"] = (
        "Manual observations only. Active asks and sold listings are separate. "
        "A sold listing's displayed price is not guaranteed to equal the final transaction amount."
    )
    PRICES_PATH.write_text(json.dumps(prices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {imported}; skipped {skipped}. Updated {PRICES_PATH}")

if __name__ == "__main__":
    main()

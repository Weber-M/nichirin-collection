# 日輪バトルスラッシュ — My Collection PWA

Phone-friendly collection tracker for A01/A02, designed so future sets can be added to `data/catalog.json`.

## Price updates

`tools/update_prices.py` reads publicly available shop collection pages and writes `data/prices.json`. The included GitHub Actions workflow can run it daily and on demand. It does not bypass login, bot protection, or access controls. Shop availability and terms can change, so price data is reference/listed pricing rather than a guaranteed transaction price.

## Install

Host the folder on an HTTPS static host such as GitHub Pages. Open the HTTPS site on Android/Chrome and use the browser's **Install app / Add to Home screen** option.

## Future sets

Add cards and a set entry to `data/catalog.json`; no gallery code rebuild is needed.

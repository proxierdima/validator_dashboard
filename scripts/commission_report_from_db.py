#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import multiprocessing

import requests
from dotenv import load_dotenv
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.models import Network, NetworkAsset, NetworkEndpoint, TrackedNetwork, Validator

load_dotenv()

CG_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
CG_SEARCH_URL = "https://api.coingecko.com/api/v3/search"

CG_API_KEY = os.getenv("CG_API_KEY")
if not CG_API_KEY:
    raise RuntimeError("CG_API_KEY not set")

REQUEST_TIMEOUT = 10
MAX_WORKERS = min(16, multiprocessing.cpu_count() * 2)

IBC_STABLE_SYMBOLS = {"USDC", "USDT", "DAI"}
IBC_STABLE_CG_IDS = {"usd-coin", "tether", "dai"}
DENOM_BLACKLIST_PREFIX = ("pool/",)


def http_get(url, params=None):
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_prices_by_ids(ids: set[str]) -> dict[str, float]:
    if not ids:
        return {}
    data = http_get(
        CG_PRICE_URL,
        {
            "ids": ",".join(sorted(ids)),
            "vs_currencies": "usd",
            "x_cg_demo_api_key": CG_API_KEY,
        },
    )
    return {k: data.get(k, {}).get("usd", 0) for k in ids} if data else {}


def search_price_by_symbol(symbol: str) -> str | None:
    data = http_get(CG_SEARCH_URL, {"query": symbol})
    if not data:
        return None
    for c in data.get("coins", []):
        if c.get("symbol", "").lower() == symbol.lower():
            return c.get("id")
    return None


def get_sources_from_db():
    db = SessionLocal()
    try:
        tracked_rows = db.execute(
            select(
                TrackedNetwork.network_id,
                Network.name,
                Network.display_name,
                Network.chain_id,
                Validator.operator_address,
            )
            .join(Network, Network.id == TrackedNetwork.network_id)
            .join(
                Validator,
                (Validator.network_id == Network.id)
                & (Validator.is_main == 1)
                & (Validator.is_enabled == 1),
            )
            .where(TrackedNetwork.is_enabled == 1)
            .where(TrackedNetwork.use_for_validator_search == 1)
            .where(Network.is_enabled == 1)
            .order_by(Network.name.asc())
        ).all()

        result = []

        for row in tracked_rows:
            rest_url = db.execute(
                select(NetworkEndpoint.url)
                .where(NetworkEndpoint.network_id == row.network_id)
                .where(NetworkEndpoint.endpoint_type == "rest")
                .where(NetworkEndpoint.is_public == 1)
                .where(NetworkEndpoint.is_enabled == 1)
                .order_by(NetworkEndpoint.priority.asc(), NetworkEndpoint.id.asc())
            ).scalars().first()

            assets = db.execute(
                select(NetworkAsset)
                .where(NetworkAsset.network_id == row.network_id)
            ).scalars().all()

            asset_map = {}
            for asset in assets:
                asset_map[asset.base_denom] = {
                    "display_denom": asset.display_denom or asset.base_denom,
                    "exponent": int(asset.exponent or 0),
                    "symbol": asset.symbol,
                    "coingecko_id": asset.coingecko_id,
                }

            result.append(
                {
                    "network_id": row.network_id,
                    "network_name": row.display_name or row.name,
                    "network_slug": row.name,
                    "chain_id": row.chain_id,
                    "valoper": row.operator_address,
                    "rest": rest_url,
                    "asset_map": asset_map,
                }
            )

        return result
    finally:
        db.close()


def get_commission(rest_url: str, valoper: str):
    if not rest_url:
        return [], "no_public_rest"

    data = http_get(
        f"{rest_url}/cosmos/distribution/v1beta1/validators/{valoper}/commission"
    )
    if data:
        return data.get("commission", {}).get("commission", []), None
    return [], "commission_query_failed"


def process_network(item: dict):
    network_name = item["network_name"]

    if not item["valoper"]:
        return [], {
            "network": network_name,
            "chain_id": item["chain_id"],
            "reason": "no_valoper_in_db",
        }

    commissions, error = get_commission(item["rest"], item["valoper"])
    if not commissions:
        return [], {
            "network": network_name,
            "chain_id": item["chain_id"],
            "valoper": item["valoper"],
            "rest": item["rest"],
            "reason": error or "no_commission",
        }

    rows = []
    for c in commissions:
        denom = c.get("denom")
        raw_amount = c.get("amount")

        if not denom or raw_amount is None:
            continue

        if denom.startswith(DENOM_BLACKLIST_PREFIX):
            continue

        try:
            raw = float(raw_amount)
        except Exception:
            continue

        meta = item["asset_map"].get(denom, {})
        display = meta.get("display_denom", denom)
        exponent = int(meta.get("exponent", 0) or 0)
        cg_id = meta.get("coingecko_id")

        if denom.startswith("ibc/"):
            symbol_check = (display or "").upper()
            if symbol_check not in IBC_STABLE_SYMBOLS and cg_id not in IBC_STABLE_CG_IDS:
                continue

        amount = raw / (10 ** exponent) if exponent else raw

        rows.append(
            {
                "network": network_name,
                "chain_id": item["chain_id"],
                "rest_used": item["rest"],
                "valoper": item["valoper"],
                "denom": denom,
                "display": display,
                "amount": amount,
                "cg_id": cg_id,
            }
        )

    if not rows:
        return [], {
            "network": network_name,
            "chain_id": item["chain_id"],
            "valoper": item["valoper"],
            "rest": item["rest"],
            "reason": "filtered_all_denoms",
        }

    return rows, None


def main():
    sources = get_sources_from_db()

    rows = []
    missing = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(process_network, item) for item in sources]
        for f in as_completed(futures):
            r, m = f.result()
            rows.extend(r)
            if m:
                missing.append(m)

    cg_ids = {r["cg_id"] for r in rows if r["cg_id"]}
    prices = fetch_prices_by_ids(cg_ids)

    totals_by_network = {}
    grand_total = 0.0

    for r in rows:
        price = prices.get(r["cg_id"], 0)

        if r["display"].upper() in IBC_STABLE_SYMBOLS:
            price = 1.0

        if price == 0 and r["cg_id"] is None:
            fallback = search_price_by_symbol(r["display"])
            if fallback:
                price = fetch_prices_by_ids({fallback}).get(fallback, 0)

        price = round(price, 10)
        total = round(r["amount"] * price, 2)

        r["price"] = price
        r["total"] = total

        totals_by_network.setdefault(r["network"], 0)
        totals_by_network[r["network"]] += total
        grand_total += total

    rows.sort(key=lambda r: r["total"], reverse=True)
    totals_by_network = dict(sorted(totals_by_network.items(), key=lambda x: x[1], reverse=True))

    with open("commission_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rows": rows,
                "totals_by_network": totals_by_network,
                "grand_total": round(grand_total, 2),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    with open("missing_networks.json", "w", encoding="utf-8") as f:
        json.dump(missing, f, indent=2, ensure_ascii=False)

    print(
        json.dumps(
            {
                "status": "ok",
                "rows_count": len(rows),
                "missing_count": len(missing),
                "grand_total": round(grand_total, 2),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from sqlalchemy import delete, or_, select

from app.core.db import SessionLocal
from app.models import Network, NetworkAsset, NetworkEndpoint

CHAIN_REGISTRY_DIR = Path("./chain-registry")
GIT_URL = "https://github.com/cosmos/chain-registry.git"
NETWORKS_FILE = Path("./config/posthuman_network_names.txt")

HTTP_TIMEOUT = 6
MAX_WORKERS = 20


def ensure_repo() -> None:
    if not CHAIN_REGISTRY_DIR.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", GIT_URL, str(CHAIN_REGISTRY_DIR)],
            check=True,
        )
        return

    try:
        subprocess.run(
            ["git", "-C", str(CHAIN_REGISTRY_DIR), "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        branch_result = subprocess.run(
            ["git", "-C", str(CHAIN_REGISTRY_DIR), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        branch = branch_result.stdout.strip()

        if branch == "HEAD":
            shutil.rmtree(CHAIN_REGISTRY_DIR, ignore_errors=True)
            subprocess.run(
                ["git", "clone", "--depth", "1", GIT_URL, str(CHAIN_REGISTRY_DIR)],
                check=True,
            )
            return

        subprocess.run(
            ["git", "-C", str(CHAIN_REGISTRY_DIR), "pull", "--ff-only"],
            check=True,
        )

    except Exception:
        shutil.rmtree(CHAIN_REGISTRY_DIR, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", GIT_URL, str(CHAIN_REGISTRY_DIR)],
            check=True,
        )


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def first_nonempty(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def load_allowed_directories() -> list[str]:
    if not NETWORKS_FILE.exists():
        raise FileNotFoundError(f"Missing file: {NETWORKS_FILE}")

    result: list[str] = []
    for line in NETWORKS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.append(line)

    return result


def parse_endpoints(chain_json: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    apis = chain_json.get("apis") or {}

    for endpoint_type in ("rpc", "rest", "grpc"):
        items = apis.get(endpoint_type) or []
        for idx, item in enumerate(items, start=1):
            address = (item.get("address") or "").strip()
            if not address:
                continue

            result.append(
                {
                    "endpoint_type": endpoint_type,
                    "label": f"{endpoint_type}{idx}",
                    "url": address,
                }
            )
    return result


def extract_asset_meta(assetlist_json: dict[str, Any] | None, base_denom: str | None) -> tuple[str | None, int | None, str | None, list[dict[str, Any]]]:
    """
    Возвращает:
    - display_denom
    - exponent
    - coingecko_id
    - assets_for_db
    """
    if not assetlist_json:
        return None, None, None, []

    assets = assetlist_json.get("assets") or []
    assets_for_db: list[dict[str, Any]] = []

    display_denom = None
    exponent = None
    coingecko_id = None

    for asset in assets:
        base = asset.get("base")
        symbol = asset.get("symbol")
        cg_id = asset.get("coingecko_id")
        asset_display = asset.get("display")

        denom_units = asset.get("denom_units") or []
        exp_value = None
        if asset_display:
            for du in denom_units:
                if du.get("denom") == asset_display:
                    exp_value = du.get("exponent")
                    break

        if exp_value is None and denom_units:
            exp_value = max((du.get("exponent", 0) or 0) for du in denom_units)

        try:
            exp_value = int(exp_value) if exp_value is not None else None
        except Exception:
            exp_value = None

        assets_for_db.append(
            {
                "base_denom": base,
                "display_denom": asset_display or base,
                "symbol": symbol,
                "exponent": exp_value,
                "coingecko_id": cg_id,
            }
        )

        if base_denom and base == base_denom:
            display_denom = asset_display or base
            exponent = exp_value
            coingecko_id = cg_id

    return display_denom, exponent, coingecko_id, assets_for_db


def check_rpc(url: str) -> bool:
    candidates = [
        url,
        f"{url.rstrip('/')}/status",
    ]
    headers = {"User-Agent": "validator-dashboard/1.0"}

    for candidate in candidates:
        try:
            r = requests.get(candidate, timeout=HTTP_TIMEOUT, headers=headers)
            if r.status_code == 200:
                body = r.text.lower()
                if "jsonrpc" in body or "node_info" in body or "latest_block_height" in body or "result" in body:
                    return True
        except Exception:
            pass
    return False


def check_rest(url: str) -> bool:
    candidates = [
        f"{url.rstrip('/')}/cosmos/base/tendermint/v1beta1/node_info",
        f"{url.rstrip('/')}/cosmos/base/node/v1beta1/config",
        f"{url.rstrip('/')}/node_info",
    ]
    headers = {"User-Agent": "validator-dashboard/1.0"}

    for candidate in candidates:
        try:
            r = requests.get(candidate, timeout=HTTP_TIMEOUT, headers=headers)
            if r.status_code == 200:
                return True
        except Exception:
            pass
    return False


def check_grpc(url: str) -> bool:
    try:
        parsed = urlparse(url if "://" in url else f"tcp://{url}")
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            return False

        with socket.create_connection((host, port), timeout=4):
            return True
    except Exception:
        return False


def is_endpoint_working(endpoint_type: str, url: str) -> bool:
    if endpoint_type == "rpc":
        return check_rpc(url)
    if endpoint_type == "rest":
        return check_rest(url)
    if endpoint_type == "grpc":
        return check_grpc(url)
    return False


def process_chain_file(chain_file: Path) -> dict[str, Any] | None:
    if chain_file.parts[-2].startswith("."):
        return None

    data = load_json(chain_file)
    if not data:
        return None

    directory = chain_file.parent.name

    name = first_nonempty(
        data.get("chain_name"),
        data.get("name"),
        directory,
    )
    if not name:
        return None

    display_name = first_nonempty(
        data.get("pretty_name"),
        data.get("display_name"),
        name,
    )

    chain_id = data.get("chain_id")
    chain_type = "cosmos"

    fees = data.get("fees") or {}
    fee_tokens = fees.get("fee_tokens") or []
    base_denom = None
    display_denom = None
    exponent = None
    coingecko_id = None

    if fee_tokens:
        fee0 = fee_tokens[0]
        base_denom = fee0.get("denom")
        display_denom = first_nonempty(
            fee0.get("display_denom"),
            fee0.get("symbol"),
            fee0.get("denom"),
        )

    assetlist_file = chain_file.parent / "assetlist.json"
    assetlist_data = load_json(assetlist_file) if assetlist_file.exists() else None

    asset_display, asset_exponent, asset_cg_id, assets_for_db = extract_asset_meta(
        assetlist_json=assetlist_data,
        base_denom=base_denom,
    )

    if asset_display:
        display_denom = asset_display
    if asset_exponent is not None:
        exponent = asset_exponent
    if asset_cg_id:
        coingecko_id = asset_cg_id

    endpoints = parse_endpoints(data)

    checked_endpoints: list[dict[str, Any]] = []
    working_rpc = None
    working_rest = None
    working_grpc = None

    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {
            pool.submit(is_endpoint_working, ep["endpoint_type"], ep["url"]): ep
            for ep in endpoints
        }

        for future in as_completed(future_map):
            ep = future_map[future]
            working = False
            try:
                working = future.result()
            except Exception:
                working = False

            checked_endpoints.append(
                {
                    "endpoint_type": ep["endpoint_type"],
                    "label": ep["label"],
                    "url": ep["url"],
                    "working": working,
                }
            )

    checked_endpoints.sort(
        key=lambda x: (
            {"rpc": 1, "rest": 2, "grpc": 3}.get(x["endpoint_type"], 99),
            x["label"],
        )
    )

    for ep in checked_endpoints:
        if ep["working"]:
            if ep["endpoint_type"] == "rpc" and working_rpc is None:
                working_rpc = ep["url"]
            elif ep["endpoint_type"] == "rest" and working_rest is None:
                working_rest = ep["url"]
            elif ep["endpoint_type"] == "grpc" and working_grpc is None:
                working_grpc = ep["url"]

    return {
        "directory": directory,
        "name": name,
        "display_name": display_name,
        "chain_id": chain_id,
        "chain_type": chain_type,
        "base_denom": base_denom,
        "display_denom": display_denom,
        "exponent": exponent,
        "coingecko_id": coingecko_id,
        "rpc": working_rpc,
        "rest": working_rest,
        "grpc": working_grpc,
        "assets_for_db": assets_for_db,
        "endpoints": checked_endpoints,
    }


def main() -> None:
    ensure_repo()

    allowed = load_allowed_directories()
    allowed_norm = {norm(x) for x in allowed}

    chain_files_all = sorted(CHAIN_REGISTRY_DIR.glob("*/chain.json"))
    chain_files = [
        p for p in chain_files_all
        if norm(p.parent.name) in allowed_norm
    ]

    found_dirs = {norm(p.parent.name) for p in chain_files}
    missing = [x for x in allowed if norm(x) not in found_dirs]

    print(f"Allowed networks: {len(allowed)}")
    print(f"Matched chain-registry dirs: {len(chain_files)}")

    if missing:
        print("\nNot found in chain-registry:")
        for item in missing:
            print(f"  - {item}")

    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_chain_file, cf): cf for cf in chain_files}
        for future in as_completed(futures):
            chain_file = futures[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
                    print(
                        f"[OK] {result['directory']}: "
                        f"rpc={result['rpc'] or '-'} "
                        f"rest={result['rest'] or '-'} "
                        f"grpc={result['grpc'] or '-'} "
                        f"exp={result['exponent'] if result['exponent'] is not None else '-'} "
                        f"cg={result['coingecko_id'] or '-'}"
                    )
            except Exception as e:
                print(f"[ERR] {chain_file.parent.name}: {e}")

    db = SessionLocal()
    try:
        for item in results:
            directory = item["directory"]
            name = item["name"]
            chain_id = item["chain_id"]

            network = db.execute(
                select(Network).where(
                    or_(
                        Network.directory == directory,
                        Network.chain_id == chain_id,
                        Network.name == name,
                    )
                )
            ).scalar_one_or_none()

            if network is None:
                network = Network(
                    name=name,
                    display_name=item["display_name"],
                    directory=directory,
                    chain_id=chain_id,
                    chain_type=item["chain_type"],
                    base_denom=item["base_denom"],
                    display_denom=item["display_denom"],
                    exponent=item["exponent"],
                    coingecko_id=item["coingecko_id"],
                    rpc=item["rpc"],
                    rest=item["rest"],
                    grpc=item["grpc"],
                    is_enabled=1,
                )
                db.add(network)
                db.flush()
            else:
                network.name = name
                network.display_name = item["display_name"]
                network.directory = directory
                network.chain_id = item["chain_id"]
                network.chain_type = item["chain_type"]
                network.base_denom = item["base_denom"]
                network.display_denom = item["display_denom"]
                network.exponent = item["exponent"]
                network.coingecko_id = item["coingecko_id"]
                network.rpc = item["rpc"]
                network.rest = item["rest"]
                network.grpc = item["grpc"]
                network.is_enabled = 1
                db.flush()

            db.execute(
                delete(NetworkEndpoint).where(NetworkEndpoint.network_id == network.id)
            )

            for ep in item["endpoints"]:
                db.add(
                    NetworkEndpoint(
                        network_id=network.id,
                        endpoint_type=ep["endpoint_type"],
                        label=ep["label"],
                        url=ep["url"],
                        priority=1,
                        is_public=1,
                        is_enabled=1 if ep["working"] else 0,
                    )
                )

            # перезаполняем network_assets из assetlist
            db.execute(
                delete(NetworkAsset).where(NetworkAsset.network_id == network.id)
            )

            for asset in item["assets_for_db"]:
                db.add(
                    NetworkAsset(
                        network_id=network.id,
                        base_denom=asset["base_denom"],
                        display_denom=asset["display_denom"],
                        symbol=asset["symbol"],
                        exponent=asset["exponent"],
                        coingecko_id=asset["coingecko_id"],
                    )
                )

        db.commit()
        print("\nChain registry import complete")
        print(f"Imported networks: {len(results)}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

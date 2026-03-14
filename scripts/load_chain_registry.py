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
CHAIN_REGISTRY_TESTNETS_DIR = CHAIN_REGISTRY_DIR / "testnets"
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


def join_values(values: list[Any]) -> str | None:
    cleaned: list[str] = []
    for v in values:
        if v in (None, "", [], {}):
            continue
        cleaned.append(str(v))
    return ",".join(cleaned) if cleaned else None


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


def detect_network_type(chain_file: Path, chain_json: dict[str, Any] | None = None) -> str:
    """
    Берем network_type из chain.json, если есть.
    Иначе:
    - если путь содержит testnets -> testnet
    - иначе -> mainnet
    """
    value = (chain_json or {}).get("network_type")
    if isinstance(value, str) and value.strip():
        v = value.strip().lower()
        if "test" in v:
            return "testnet"
        if "main" in v:
            return "mainnet"
        return v

    parts = {p.lower() for p in chain_file.parts}
    return "testnet" if "testnets" in parts else "mainnet"


def parse_endpoints(chain_json: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    apis = chain_json.get("apis") or {}
    for endpoint_type in ("rpc", "rest", "grpc"):
        items = apis.get(endpoint_type) or []

        counter = 0
        for item in items:
            address = (item.get("address") or "").strip()
            if not address:
                continue

            normalized = address.rstrip("/")
            key = (endpoint_type, normalized)
            if key in seen:
                continue

            seen.add(key)
            counter += 1

            result.append(
                {
                    "endpoint_type": endpoint_type,
                    "label": f"{endpoint_type}{counter}",
                    "url": normalized,
                }
            )

    return result


def extract_asset_meta(
    assetlist_json: dict[str, Any] | None,
    base_denom: str | None,
) -> tuple[str | None, int | None, str | None, list[dict[str, Any]]]:
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

    deduped_assets: list[dict[str, Any]] = []
    seen_assets: set[tuple[str | None, str | None, str | None]] = set()

    for asset in assets_for_db:
        key = (
            asset.get("base_denom"),
            asset.get("display_denom"),
            asset.get("symbol"),
        )
        if key in seen_assets:
            continue
        seen_assets.add(key)
        deduped_assets.append(asset)

    return display_denom, exponent, coingecko_id, deduped_assets


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
                if (
                    "jsonrpc" in body
                    or "node_info" in body
                    or "latest_block_height" in body
                    or "result" in body
                ):
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


def build_match_keys(chain_file: Path, chain_json: dict[str, Any] | None = None) -> set[str]:
    keys: set[str] = set()

    directory = chain_file.parent.name
    keys.add(norm(directory))

    is_testnet = chain_file.parent.parent.name == "testnets"

    if chain_json:
        for value in (
            chain_json.get("chain_name"),
            chain_json.get("name"),
            chain_json.get("pretty_name"),
            chain_json.get("display_name"),
            chain_json.get("chain_id"),
        ):
            n = norm(value)
            if n:
                keys.add(n)

    if is_testnet:
        base_keys = list(keys)
        for k in base_keys:
            if k and not k.endswith("testnet"):
                keys.add(f"{k}testnet")

    return keys


def collect_chain_files() -> list[Path]:
    mainnet_files = sorted(CHAIN_REGISTRY_DIR.glob("*/chain.json"))
    testnet_files = sorted(CHAIN_REGISTRY_TESTNETS_DIR.glob("*/chain.json"))
    return mainnet_files + testnet_files


def pick_first_two(endpoints: list[dict[str, Any]], endpoint_type: str) -> tuple[str | None, str | None]:
    urls = [x["url"] for x in endpoints if x["endpoint_type"] == endpoint_type and x.get("working")]
    if not urls:
        return None, None
    if len(urls) == 1:
        return urls[0], None
    return urls[0], urls[1]


def process_chain_file(chain_file: Path) -> dict[str, Any] | None:
    parent_name = chain_file.parent.name
    if parent_name.startswith("."):
        return None

    data = load_json(chain_file)
    if not data:
        return None

    directory = parent_name
    is_testnet = chain_file.parent.parent.name == "testnets"

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
    chain_type = first_nonempty(data.get("chain_type"), "cosmos")
    network_type = detect_network_type(chain_file, data)
    status = data.get("status")
    website = data.get("website")
    bech32_prefix = data.get("bech32_prefix")
    daemon_name = data.get("daemon_name")
    node_home = data.get("node_home")
    key_algos = join_values(data.get("key_algos") or [])

    try:
        slip44 = int(data.get("slip44")) if data.get("slip44") is not None else None
    except Exception:
        slip44 = None

    fees = data.get("fees") or {}
    fee_tokens_raw = fees.get("fee_tokens") or []

    base_denom = None
    display_denom = None
    exponent = None
    coingecko_id = None

    if fee_tokens_raw:
        fee0 = fee_tokens_raw[0]
        base_denom = fee0.get("denom")
        display_denom = first_nonempty(
            fee0.get("display_denom"),
            fee0.get("symbol"),
            fee0.get("denom"),
        )

    fee_tokens = join_values([x.get("denom") for x in fee_tokens_raw])
    fixed_min_gas_price = join_values([x.get("fixed_min_gas_price") for x in fee_tokens_raw])
    low_gas_price = join_values([x.get("low_gas_price") for x in fee_tokens_raw])
    average_gas_price = join_values([x.get("average_gas_price") for x in fee_tokens_raw])
    high_gas_price = join_values([x.get("high_gas_price") for x in fee_tokens_raw])

    staking = data.get("staking") or {}
    staking_tokens_raw = staking.get("staking_tokens") or []
    staking_tokens = join_values([x.get("denom") for x in staking_tokens_raw])

    codebase = data.get("codebase") or {}
    git_repo = codebase.get("git_repo")
    recommended_version = codebase.get("recommended_version")
    compatible_versions = join_values(codebase.get("compatible_versions") or [])
    genesis_url = ((codebase.get("genesis") or {}).get("genesis_url"))

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

    rpc1, rpc2 = pick_first_two(checked_endpoints, "rpc")
    rest1, rest2 = pick_first_two(checked_endpoints, "rest")
    grpc1, grpc2 = pick_first_two(checked_endpoints, "grpc")

    return {
        "directory": directory,
        "name": name,
        "display_name": display_name,
        "chain_id": chain_id,
        "chain_type": chain_type,
        "network_type": network_type,
        "status": status,
        "website": website,
        "bech32_prefix": bech32_prefix,
        "daemon_name": daemon_name,
        "node_home": node_home,
        "key_algos": key_algos,
        "slip44": slip44,
        "base_denom": base_denom,
        "display_denom": display_denom,
        "exponent": exponent,
        "coingecko_id": coingecko_id,
        "fee_tokens": fee_tokens,
        "fixed_min_gas_price": fixed_min_gas_price,
        "low_gas_price": low_gas_price,
        "average_gas_price": average_gas_price,
        "high_gas_price": high_gas_price,
        "staking_tokens": staking_tokens,
        "git_repo": git_repo,
        "recommended_version": recommended_version,
        "compatible_versions": compatible_versions,
        "genesis_url": genesis_url,
        "rpc": working_rpc,
        "rest": working_rest,
        "grpc": working_grpc,
        "rpc1": rpc1,
        "rest1": rest1,
        "grpc1": grpc1,
        "rpc2": rpc2,
        "rest2": rest2,
        "grpc2": grpc2,
        "assets_for_db": assets_for_db,
        "endpoints": checked_endpoints,
        "registry_scope": "testnet" if is_testnet else "mainnet",
    }


def main() -> None:
    ensure_repo()

    allowed = load_allowed_directories()
    allowed_norm = {norm(x) for x in allowed}

    chain_files_all = collect_chain_files()

    chain_files: list[Path] = []
    found_allowed: set[str] = set()

    for p in chain_files_all:
        data = load_json(p)
        match_keys = build_match_keys(p, data)

        matched = allowed_norm.intersection(match_keys)
        if matched:
            chain_files.append(p)
            found_allowed.update(matched)

    missing = [x for x in allowed if norm(x) not in found_allowed]

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
                        f"[OK] {result['directory']} ({result['registry_scope']}): "
                        f"network_type={result['network_type']} "
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
                    network_type=item["network_type"],
                    status=item["status"],
                    website=item["website"],
                    bech32_prefix=item["bech32_prefix"],
                    daemon_name=item["daemon_name"],
                    node_home=item["node_home"],
                    key_algos=item["key_algos"],
                    slip44=item["slip44"],
                    base_denom=item["base_denom"],
                    display_denom=item["display_denom"],
                    exponent=item["exponent"],
                    coingecko_id=item["coingecko_id"],
                    fee_tokens=item["fee_tokens"],
                    fixed_min_gas_price=item["fixed_min_gas_price"],
                    low_gas_price=item["low_gas_price"],
                    average_gas_price=item["average_gas_price"],
                    high_gas_price=item["high_gas_price"],
                    staking_tokens=item["staking_tokens"],
                    git_repo=item["git_repo"],
                    recommended_version=item["recommended_version"],
                    compatible_versions=item["compatible_versions"],
                    genesis_url=item["genesis_url"],
                    rpc=item["rpc"],
                    rest=item["rest"],
                    grpc=item["grpc"],
                    rpc1=item["rpc1"],
                    rest1=item["rest1"],
                    grpc1=item["grpc1"],
                    rpc2=item["rpc2"],
                    rest2=item["rest2"],
                    grpc2=item["grpc2"],
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
                network.network_type = item["network_type"]
                network.status = item["status"]
                network.website = item["website"]
                network.bech32_prefix = item["bech32_prefix"]
                network.daemon_name = item["daemon_name"]
                network.node_home = item["node_home"]
                network.key_algos = item["key_algos"]
                network.slip44 = item["slip44"]
                network.base_denom = item["base_denom"]
                network.display_denom = item["display_denom"]
                network.exponent = item["exponent"]
                network.coingecko_id = item["coingecko_id"]
                network.fee_tokens = item["fee_tokens"]
                network.fixed_min_gas_price = item["fixed_min_gas_price"]
                network.low_gas_price = item["low_gas_price"]
                network.average_gas_price = item["average_gas_price"]
                network.high_gas_price = item["high_gas_price"]
                network.staking_tokens = item["staking_tokens"]
                network.git_repo = item["git_repo"]
                network.recommended_version = item["recommended_version"]
                network.compatible_versions = item["compatible_versions"]
                network.genesis_url = item["genesis_url"]
                network.rpc = item["rpc"]
                network.rest = item["rest"]
                network.grpc = item["grpc"]
                network.rpc1 = item["rpc1"]
                network.rest1 = item["rest1"]
                network.grpc1 = item["grpc1"]
                network.rpc2 = item["rpc2"]
                network.rest2 = item["rest2"]
                network.grpc2 = item["grpc2"]
                network.is_enabled = 1
                db.flush()

            db.execute(
                delete(NetworkEndpoint).where(NetworkEndpoint.network_id == network.id)
            )

            seen_db_eps: set[tuple[str, str]] = set()

            for ep in item["endpoints"]:
                ep_url = (ep["url"] or "").strip().rstrip("/")
                ep_type = (ep["endpoint_type"] or "").strip()

                if not ep_url or not ep_type:
                    continue

                key = (ep_type, ep_url)
                if key in seen_db_eps:
                    continue
                seen_db_eps.add(key)

                db.add(
                    NetworkEndpoint(
                        network_id=network.id,
                        endpoint_type=ep_type,
                        label=ep["label"],
                        url=ep_url,
                        priority=1,
                        is_public=1,
                        is_enabled=1 if ep["working"] else 0,
                    )
                )

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

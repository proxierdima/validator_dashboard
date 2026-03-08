rm -f validator_dashboard.db

python3 -m scripts.init_db
python3 -m scripts.load_chain_registry
python3 -m scripts.load_tracked_networks
python3 -m scripts.load_posthuman_endpoints
python3 -m scripts.load_public_rpcs

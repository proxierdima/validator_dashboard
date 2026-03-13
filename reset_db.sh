rm -f validator_dashboard.db

python3 -m scripts.init_db
python3 -m scripts.load_chain_registry
python3 -m scripts.load_tracked_networks
python3 -m scripts.load_posthuman_endpoints
python3 -m scripts.load_public_rpcs
python3 -m app.collectors.endpoint_health_collector
python3 -m app.collectors.validator_status_collector
python3 -m app.collectors.governance_collector
python3 -m app.collectors.reward_status_collector
python3 -m app.services.network_status_aggregator
python3 -m app.tasks.run_health_cycle

from app.collectors.endpoint_health_collector import main as endpoint_main
from app.collectors.validator_status_collector import main as validator_main
from app.services.network_status_aggregator import main as aggregator_main


def main() -> None:
    endpoint_main()
    validator_main()
    aggregator_main()


if __name__ == "__main__":
    main()

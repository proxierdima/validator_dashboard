# Validator Dashboard

Monitoring panel for Cosmos validator infrastructure.

Features:

- validator monitoring
- RPC health checks
- commission tracking
- rewards reporting
- alert events

Tech stack:

- Python
- FastAPI
- SQLite
- Jinja2

## Database schema and data flow

```mermaid
flowchart LR

    %% =========================
    %% SOURCES
    %% =========================
    subgraph SOURCES[Sources]
        S1[Cosmos chain-registry]
        S2[config/posthuman_endpoints.txt]
        S3[Tracked networks config / bootstrap]
        S4[RPC endpoints]
        S5[REST endpoints]
        S6[Snapshot storage]
        S7[Governance APIs]
    end

    %% =========================
    %% BOOTSTRAP / LOADERS
    %% =========================
    subgraph LOADERS[Bootstrap loaders]
        L0[init_db.py]
        L1[load_chain_registry.py]
        L2[load_tracked_networks.py]
        L3[load_posthuman_endpoints.py]
        L4[load_public_rpcs.py]
    end

    %% =========================
    %% COLLECTORS
    %% =========================
    subgraph COLLECTORS[Collectors / aggregators]
        C1[endpoint_health_collector.py]
        C2[validator_status_collector.py]
        C3[snapshot collector]
        C4[governance collector]
        C5[network_status_aggregator.py]
        C6[run_health_cycle]
    end

    %% =========================
    %% CORE TABLES
    %% =========================
    subgraph DB[Database tables]
        N[(networks)]
        NA[(network_assets)]
        TN[(tracked_networks)]

        NE[(network_endpoints)]
        EC[(endpoint_checks)]

        PR[(public_rpc_endpoints)]
        PRC[(public_rpc_checks)]

        V[(validators)]
        VSC[(validator_status_current)]
        VSH[(validator_status_history)]

        ST[(snapshot_targets)]
        SC[(snapshot_checks)]

        GP[(governance_proposals)]
        EV[(events)]
        CR[(collector_runs)]

        NS[(network_status_current)]
    end

    %% =========================
    %% UI
    %% =========================
    subgraph UI[Application / UI]
        U1[FastAPI routes]
        U2[Dashboard pages]
        U3[Alerts / reports]
    end

    %% =========================
    %% INIT
    %% =========================
    L0 --> N
    L0 --> NA
    L0 --> TN
    L0 --> NE
    L0 --> EC
    L0 --> PR
    L0 --> PRC
    L0 --> V
    L0 --> VSC
    L0 --> VSH
    L0 --> ST
    L0 --> SC
    L0 --> GP
    L0 --> EV
    L0 --> CR
    L0 --> NS

    %% =========================
    %% SOURCES -> LOADERS
    %% =========================
    S1 --> L1
    S2 --> L3
    S3 --> L2
    S4 --> C1
    S4 --> C2
    S5 --> C1
    S5 --> C2
    S6 --> C3
    S7 --> C4

    %% =========================
    %% LOADERS -> TABLES
    %% =========================
    L1 --> N
    L1 --> NA
    L1 --> NE

    L2 --> TN

    L3 --> V
    L3 --> NE

    L4 --> PR

    %% =========================
    %% COLLECTORS -> TABLES
    %% =========================
    C1 --> EC
    C1 --> CR

    C2 --> V
    C2 --> VSC
    C2 --> VSH
    C2 --> CR

    C3 --> SC
    C3 --> CR

    C4 --> GP
    C4 --> CR

    C5 --> NS
    C6 --> EV
    C6 --> NS

    %% =========================
    %% RELATIONS
    %% =========================
    N --> NA
    N --> TN
    N --> NE
    N --> PR
    N --> V
    N --> ST
    N --> GP
    N --> NS
    N --> EV

    NE --> EC
    PR --> PRC
    V --> VSC
    V --> VSH
    V --> EV
    ST --> SC

    %% =========================
    %% DB -> UI
    %% =========================
    NS --> U1
    EV --> U1
    VSC --> U1
    EC --> U1
    GP --> U1

    U1 --> U2
    U1 --> U3

```

---

## 2) ASCII-схема для терминала / документации

```md
## Database schema and data flow (ASCII)
```

```text
                             +----------------------+
                             |  Cosmos chain-registry |
                             +-----------+----------+
                                         |
                                         v
                              +----------------------+
                              | load_chain_registry  |
                              +----+-----------+-----+
                                   |           |
                                   |           |
                                   v           v
                           +-----------+   +-----------+
                           | networks  |-->|network_assets|
                           +-----+-----+   +-------------+
                                 |
                                 v
                         +---------------+
                         |network_endpoints|
                         +-------+-------+
                                 |
                                 |
               +-----------------+------------------+
               |                                    |
               v                                    v
     +----------------------+             +----------------------+
     | endpoint_health_     |             | load_public_rpcs.py  |
     | collector.py         |             +----------+-----------+
     +----------+-----------+                        |
                |                                    v
                v                           +----------------------+
       +-------------------+                | public_rpc_endpoints |
       | endpoint_checks   |                +----------+-----------+
       +-------------------+                           |
                |                                      v
                |                             +-------------------+
                |                             | public_rpc_checks |
                |                             +-------------------+
                |
                v
       +-------------------+
       | collector_runs    |
       +-------------------+


+------------------------------+
| config/posthuman_endpoints   |
+---------------+--------------+
                |
                v
    +-------------------------------+
    | load_posthuman_endpoints.py   |
    +-------------+-----------------+
                  | 
                  +------------------------+
                  |                        |
                  v                        v
          +---------------+        +------------------+
          | validators    |        | network_endpoints|
          +-------+-------+        +------------------+
                  |
                  v
      +-----------------------------+
      | validator_status_collector  |
      +---------+----------+--------+
                |          |
                |          |
                v          v
     +----------------+   +------------------------+
     |validator_status|   |validator_status_history|
     |_current        |   +------------------------+
     +--------+-------+
              |
              v
      +-------------------+
      | collector_runs    |
      +-------------------+


+------------------------------+
| load_tracked_networks.py     |
+---------------+--------------+
                |
                v
        +------------------+
        | tracked_networks |
        +------------------+


+------------------------------+
| snapshot_targets            |
+---------------+--------------+
                |
                v
      +------------------------+
      | snapshot collector     |
      +-----------+------------+
                  |
                  v
          +------------------+
          | snapshot_checks  |
          +------------------+


+------------------------------+
| governance collector         |
+---------------+--------------+
                |
                v
       +-----------------------+
       | governance_proposals  |
       +-----------------------+


                    +------------------------------+
                    | network_status_aggregator.py |
                    +---------------+--------------+
                                    |
                                    v
                         +------------------------+
                         | network_status_current |
                         +-----------+------------+
                                     |
                                     v
                           +----------------------+
                           | FastAPI / Dashboard  |
                           +----------+-----------+
                                      |
                 +--------------------+--------------------+
                 |                                         |
                 v                                         v
        +------------------+                      +------------------+
        | dashboard pages  |                      | alerts / reports |
        +------------------+                      +------------------+


Additional event flow:
----------------------
run_health_cycle
      |
      v
+------------+
|  events    |
+------------+
      |
      v
FastAPI / Dashboard

```
## System Architecture


```mermaid
flowchart LR

subgraph Sources
A1[Cosmos chain-registry]
A2[PostHuman endpoint config]
A3[RPC endpoints]
A4[REST endpoints]
A5[Snapshot storage]
A6[Governance APIs]
end

subgraph Bootstrap
B1[init_db]
B2[load_chain_registry]
B3[load_tracked_networks]
B4[load_posthuman_endpoints]
B5[load_public_rpcs]
end

subgraph ReferenceData
C1[(networks)]
C2[(network_assets)]
C3[(tracked_networks)]
C4[(network_endpoints)]
C5[(validators)]
end

subgraph Collectors
D1[endpoint_health_collector]
D2[validator_status_collector]
D3[snapshot_collector]
D4[governance_collector]
end

subgraph Metrics
E1[(endpoint_checks)]
E2[(validator_status_current)]
E3[(validator_status_history)]
E4[(snapshot_checks)]
E5[(governance_proposals)]
E6[(collector_runs)]
end

subgraph Aggregation
F1[network_status_aggregator]
F2[(network_status_current)]
F3[(events)]
end

subgraph Application
G1[FastAPI API]
G2[Dashboard UI]
G3[Alerts / Reports]
end

A1 --> B2
A2 --> B4
A3 --> D1
A4 --> D2
A5 --> D3
A6 --> D4

B1 --> C1
B2 --> C1
B2 --> C2
B2 --> C4

B3 --> C3
B4 --> C5
B4 --> C4

D1 --> E1
D2 --> E2
D2 --> E3
D3 --> E4
D4 --> E5

D1 --> E6
D2 --> E6
D3 --> E6
D4 --> E6

E1 --> F1
E2 --> F1
E4 --> F1
E5 --> F1

F1 --> F2
F1 --> F3

F2 --> G1
F3 --> G1

G1 --> G2
G1 --> G3
```


## Monitoring Data Pipeline

```mermaid
flowchart TD

A[chain-registry] --> B[load_chain_registry]

B --> C[(networks)]
B --> D[(network_assets)]
B --> E[(network_endpoints)]

F[posthuman_endpoints config] --> G[load_posthuman_endpoints]

G --> H[(validators)]
G --> E

I[load_tracked_networks] --> J[(tracked_networks)]

E --> K[endpoint_health_collector]

K --> L[(endpoint_checks)]
K --> M[(collector_runs)]

H --> N[validator_status_collector]

N --> O[(validator_status_current)]
N --> P[(validator_status_history)]
N --> M

Q[(snapshot_targets)] --> R[snapshot_collector]

R --> S[(snapshot_checks)]
R --> M

T[governance_collector] --> U[(governance_proposals)]
T --> M

L --> V[network_status_aggregator]
O --> V
S --> V
U --> V

V --> W[(network_status_current)]
V --> X[(events)]

W --> Y[FastAPI Dashboard]
X --> Y
```

## Entity Relationship Diagram

```mermaid
erDiagram
    NETWORKS ||--o{ NETWORK_ASSETS : has
    NETWORKS ||--o{ TRACKED_NETWORKS : tracked_in
    NETWORKS ||--o{ NETWORK_ENDPOINTS : exposes
    NETWORKS ||--o{ PUBLIC_RPC_ENDPOINTS : has
    NETWORKS ||--o{ VALIDATORS : contains
    NETWORKS ||--o{ SNAPSHOT_TARGETS : defines
    NETWORKS ||--o{ GOVERNANCE_PROPOSALS : has
    NETWORKS ||--|| NETWORK_STATUS_CURRENT : aggregates
    NETWORKS ||--o{ EVENTS : produces

    NETWORK_ENDPOINTS ||--o{ ENDPOINT_CHECKS : checked_by
    PUBLIC_RPC_ENDPOINTS ||--o{ PUBLIC_RPC_CHECKS : checked_by

    VALIDATORS ||--|| VALIDATOR_STATUS_CURRENT : current_state
    VALIDATORS ||--o{ VALIDATOR_STATUS_HISTORY : historical_state
    VALIDATORS ||--o{ EVENTS : related_to

    SNAPSHOT_TARGETS ||--o{ SNAPSHOT_CHECKS : checked_by

    NETWORKS {
        int id PK
        string name
        string display_name
        string chain_id
        string chain_type
        string base_denom
        string display_denom
        int exponent
        string coingecko_id
        bool is_enabled
        datetime created_at
        datetime updated_at
    }

    NETWORK_ASSETS {
        int id PK
        int network_id FK
        string base_denom
        string display_denom
        string symbol
        int exponent
        string coingecko_id
        datetime created_at
    }

    TRACKED_NETWORKS {
        int id PK
        int network_id FK
        string custom_name
        bool is_enabled
        bool use_for_validator_search
        bool use_for_validator_rpc_checks
        datetime created_at
        datetime updated_at
    }

    NETWORK_ENDPOINTS {
        int id PK
        int network_id FK
        string endpoint_type
        string label
        string url
        int priority
        bool is_public
        bool is_enabled
        datetime created_at
        datetime updated_at
    }

    ENDPOINT_CHECKS {
        int id PK
        int endpoint_id FK
        string status
        int http_status
        float latency_ms
        bigint remote_height
        string chain_id_reported
        string error_message
        datetime checked_at
    }

    PUBLIC_RPC_ENDPOINTS {
        int id PK
        int network_id FK
        string label
        string url
        int priority
        bool is_enabled
        string source
        datetime created_at
        datetime updated_at
    }

    PUBLIC_RPC_CHECKS {
        int id PK
        int public_rpc_endpoint_id FK
        string status
        int http_status
        float latency_ms
        bigint remote_height
        string chain_id_reported
        string error_message
        datetime checked_at
    }

    VALIDATORS {
        int id PK
        int network_id FK
        string moniker
        string operator_address
        string delegator_address
        string consensus_address
        bool is_main
        bool is_enabled
        datetime created_at
        datetime updated_at
    }

    VALIDATOR_STATUS_CURRENT {
        int validator_id PK, FK
        string status
        bool in_active_set
        bool jailed
        bool tombstoned
        numeric tokens
        numeric delegator_shares
        numeric commission_rate
        numeric commission_max_rate
        numeric commission_max_change_rate
        numeric min_self_delegation
        numeric self_delegation_amount
        int rank
        numeric voting_power
        bigint last_seen_height
        datetime last_checked_at
        json raw_json
    }

    VALIDATOR_STATUS_HISTORY {
        int id PK
        int validator_id FK
        string status
        bool in_active_set
        bool jailed
        bool tombstoned
        numeric tokens
        numeric delegator_shares
        numeric commission_rate
        int rank
        numeric voting_power
        bigint last_seen_height
        datetime collected_at
        json raw_json
    }

    SNAPSHOT_TARGETS {
        int id PK
        int network_id FK
        string snapshot_path
        string filename_pattern
        string compression_type
        bigint min_expected_size_bytes
        int max_age_hours
        bool is_enabled
        datetime created_at
        datetime updated_at
    }

    SNAPSHOT_CHECKS {
        int id PK
        int snapshot_target_id FK
        string file_name
        string file_path
        bool file_exists
        bigint file_size_bytes
        datetime file_mtime
        bigint age_seconds
        bigint size_delta_bytes
        string status
        string error_message
        datetime checked_at
    }

    GOVERNANCE_PROPOSALS {
        int id PK
        int network_id FK
        string proposal_id
        string title
        string description
        string status
        datetime voting_start_time
        datetime voting_end_time
        string validator_voter_address
        bool validator_voted
        string validator_vote_option
        datetime snapshot_at
        datetime last_updated_at
        bool is_latest
    }

    NETWORK_STATUS_CURRENT {
        int network_id PK, FK
        string validator_status
        string endpoint_status
        string sync_status
        string snapshot_status
        string governance_status
        string reward_status
        string overall_status
        bigint local_height
        bigint reference_height
        bigint sync_diff
        int active_alerts_count
        datetime last_updated_at
    }

    EVENTS {
        int id PK
        int network_id FK
        int validator_id FK
        string event_type
        string severity
        string title
        string message
        string event_key
        string status
        datetime first_seen_at
        datetime last_seen_at
        datetime resolved_at
        json metadata_json
    }
```

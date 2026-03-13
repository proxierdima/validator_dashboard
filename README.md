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

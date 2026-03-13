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



# CLAUDE.md — Cell OEE Demo ("from PLC to browser")

## Purpose

Public portfolio project. It shows the full chain from an industrial controller to a web app:
a simulated robotic welding + assembly cell exposes its data over OPC UA (as a Siemens S7-1500
would), a collector ingests it in real time, and a web app computes OEE (TRS in French) live and
lets operators qualify their stops.

This is a **demo, not a product**. Priorities, in order:
1. Domain credibility — an automation engineer must recognise a real cell and a correct OEE.
2. Runs in one command: `docker compose up`, working on a clean machine within 2 minutes.
3. Clean, readable, tested code.
4. Visual polish of the UI.

Do not add features beyond this file. If something is ambiguous in the domain model (states,
OEE rules, fault handling), **stop and ask** rather than choosing a default.

## Architecture

```
simulator (Python, asyncua)  --OPC UA subscriptions-->  collector (Python)
                                                            |
                                                            v
                                          PostgreSQL + TimescaleDB
                                                            ^
                                                            |
frontend (Next.js, TypeScript)  <--REST + WebSocket-->  api (FastAPI)
```

Five services in `docker-compose.yml`: `simulator`, `db`, `collector`, `api`, `frontend`.

## Repository layout

```
/simulator        OPC UA server + cell simulation (standalone Python package)
/backend
  /app/oee        PURE OEE logic: no I/O, no DB, fully unit-tested
  /app/collector  OPC UA client, event detection, persistence
  /app/api        FastAPI routes + WebSocket
  /app/db         models, migrations (Alembic)
/frontend         Next.js app (App Router, TypeScript)
/docs             architecture diagram, screenshots, GIF
docker-compose.yml
README.md         bilingual (English first, then French)
```

## The cell

A robotic body-in-white cell with three stations in series, controlled by a Siemens S7-1500.

- **OP10 — pre-weld assembly**: part loading, positioning, clamping, presence check.
- **OP20 — robotic resistance spot welding**: robot with a welding gun. **Bottleneck station**: it sets the cell cycle time.
- **OP30 — post-weld assembly and final check**: clipping/screwing, vision inspection, good part out or scrap.

**OEE is computed at cell level only.** Stations are used only to locate the origin of a stop
(Pareto by station / fault code).

### Cell states (`Cell.State`, Int16)

| Code | State        | OEE category                               |
|------|--------------|--------------------------------------------|
| 1    | PRODUCING    | Operating time                             |
| 2    | PLANNED_STOP | Planned stop (removed from planned time)   |
| 3    | FAULT        | Availability loss                          |
| 4    | SETUP        | Availability loss (includes cap change)    |
| 5    | STARVED      | Availability loss, flagged `induced`       |
| 6    | BLOCKED      | Availability loss, flagged `induced`       |
| 7    | MANUAL       | Availability loss                          |

`Cell.Mode`: 1 = AUTO, 2 = MANUAL.

### Fault codes

| Code | Station | Meaning                                   |
|------|---------|-------------------------------------------|
| 101  | OP10    | Part missing (presence sensor)            |
| 102  | OP10    | Clamp not in position                     |
| 103  | OP10    | Light curtain interrupted                 |
| 201  | OP20    | Weld current out of tolerance             |
| 202  | OP20    | Robot fault (stopped on error)            |
| 203  | OP20    | Gun cooling water flow fault              |
| 204  | OP20    | Tip dresser fault                         |
| 301  | OP30    | Clip missing / screwing NOK               |
| 302  | OP30    | Vision system fault                       |
| 303  | OP30    | Outfeed jam                               |
| 900  | CELL    | Emergency stop                            |

## OPC UA address space

Namespace URI: `urn:cell-oee-demo:plc`. Endpoint: `opc.tcp://simulator:4840`.
Structure mirrors an S7-1500 data block exposed through the PLC's OPC UA server, so the demo
maps directly onto a real controller.

```
Cell/
  State                 Int16
  Mode                  Int16
  FaultCode             Int16    (0 = none)
  StopOrigin            Int16    0 = none (producing), 10 = OP10, 20 = OP20, 30 = OP30,
                                 99 = cell; written at the same SourceTimestamp as State
  GoodCount             UInt32   cumulative, never reset
  ScrapCount            UInt32   cumulative, never reset
  LastCycleTime         Float    seconds
  TheoreticalCycleTime  Float    seconds
  Heartbeat             UInt32   increments every second (sim time)
OP10/ OP20/ OP30/
  State                 Int16    mirrors Cell.State (origin: see Cell.StopOrigin)
  FaultCode             Int16    non-zero on the faulty station only
OP20/Weld/
  LastPointCurrent      Float    kA
  PointsSinceCapChange  UInt32
  LastPointOK           Boolean
```

`Cell.StopOrigin` rules: FAULT → station of the fault code (99 for 900 emergency stop);
SETUP → 20; STARVED → 10; BLOCKED → 30; PLANNED_STOP → 99; MANUAL → station of the fault it
follows; PRODUCING → 0. The collector reads the origin directly, it never deduces it.

## Simulation model

All parameters live in `simulator/config.yaml`. Defaults:

| Parameter                  | Default                                        |
|----------------------------|------------------------------------------------|
| Theoretical cycle time     | 55 s                                           |
| Actual cycle time          | 55 s + small positive noise (performance loss) |
| Shifts                     | 3×8: 05:00–13:00, 13:00–21:00, 21:00–05:00     |
| Planned break              | 20 min per shift, 4 h after shift start        |
| Base scrap rate            | ~1.5 %, rising with electrode wear             |
| Tip dressing               | every ~150 parts, 30 s (becomes a micro-stop)  |
| Cap change                 | every ~1,500 parts, 5 min, state SETUP         |
| Breakdowns                 | per fault code, MTBF + duration 5–45 min       |
| Starved / blocked          | random, mostly short, occasionally long        |
| Time acceleration          | configurable, 1× to 60×                        |

**Electrode wear** is the signature behaviour: as `PointsSinceCapChange` grows, weld current
drifts and the probability of a bad weld (fault 201, scrap) increases; tip dressing partially
restores it, cap change fully restores it. The quality curve must visibly degrade and recover.

**Time**: the simulation runs on its own clock (start date configurable). Every value is written
with an OPC UA **SourceTimestamp in simulated time**. The collector and all OEE computations use
SourceTimestamp, never wall-clock time. The UI's "now" is the latest simulated time received.

Randomness must be seedable (`seed` in config) so runs are reproducible for tests and demos.

## OEE definitions (the core of the project)

OEE (TRS, *taux de rendement synthétique*) is defined **in time**, following the French standard
**NF E60-182**. Every quantity is a duration; a part is worth its theoretical cycle time (TCT).

| Time (NF E60-182)                                   | Definition                                                   |
|-----------------------------------------------------|--------------------------------------------------------------|
| Opening time                                        | length of the period, minus communication gaps (unobserved)  |
| **Required time** (*temps requis*)                  | opening time − planned stops                                 |
| **Operating time** (*temps de fonctionnement brut*) | required time − (faults + setup + starved + blocked + manual), **micro-stops excluded** |
| **Net time** (*temps net*)                          | total parts × TCT                                            |
| **Useful time** (*temps utile*)                     | good parts × TCT                                             |

- Availability = operating time ÷ required time
- Performance = net time ÷ operating time
- Quality = useful time ÷ net time
- **OEE = Availability × Performance × Quality = useful time ÷ required time**

Quality is a ratio of times, so each part is weighted by the TCT in force when it was made. With
a constant TCT (always the case in this demo) it reduces to the familiar **good parts ÷ total
parts**, and performance to (total parts × TCT) ÷ operating time. The definition in time stays
exact if the TCT changes inside a period; the parts-count shortcut does not.

**Micro-stops**: any non-planned stop shorter than **120 s** is not a stop for availability
purposes and is not shown to operators for qualification. Its time stays inside operating time
and therefore shows up as a performance loss. Threshold is configurable. A stop is classified on
its **whole** duration, never on the part that falls inside a period.

**Reference invariant (mandatory test)**:
`OEE == useful time ÷ required time == Σ(good parts × TCT) ÷ required time`, within
floating-point tolerance, for every period and grouping. If this fails, there is a bug.

**Undefined values**: `null` (never 0) when the required time is zero (empty period, or fully
planned stop / communication gap). A required time > 0 with no good part gives OEE = 0; a single
ratio is `null` if its own denominator is zero (e.g. performance with no operating time).

Other mandatory tests: periods spanning a shift boundary, a stop spanning two shifts (split
proportionally), an empty period (no division by zero: return `null`, not 0), counter rollover
and counter reset.

`/backend/app/oee` stays pure: functions take lists of events/samples and return results.

## Collector rules

- Use OPC UA **subscriptions**, not polling.
- Counters are cumulative: store raw samples, compute deltas. A decrease means a PLC restart or
  rollover — handle it explicitly, never produce negative production.
- Watch `Cell.Heartbeat`: no change for **max(5 s simulated, 3 s wall-clock)**, i.e. a wall-clock
  timeout of max(3 s, 5 s ÷ `ACCELERATION`) → mark communication lost, record a gap, exclude
  the gap from OEE and show it in the UI. The wall-clock floor is deliberate: a real
  communication loss stops simulated time, and at 60× 5 simulated seconds is only 83 ms of real
  time, below normal jitter. A gap starts at the SourceTimestamp of the last heartbeat and ends
  at the SourceTimestamp of the first heartbeat received afterwards. Every interval in which the
  collector itself was not watching is a gap too: the time between the PLC's first values and the
  collector's first heartbeat on a first run, and the time between the last heartbeat seen and
  the first one after a restart or a reconnection (simulated time jumped by more than 5 s).
- On every `Cell.State` change: close the current stop (if any), open a new one if the new state
  is a non-producing state. Record origin station (read from `Cell.StopOrigin`, never deduced)
  and fault code at stop start.
- Everything is processed and stored by **SourceTimestamp**, never by arrival time. Writes are
  idempotent (natural keys on the source timestamp): a collector restart must create no duplicate
  event and no phantom stop; the open stop is rebuilt from the database at startup.
- Reconnect automatically with backoff if the simulator restarts.
- After a committed write, `pg_notify` the `notify_channel` so the API's live view wakes up
  immediately instead of polling (payload is a wake-up hint only: `{"now": ...}`, not a data
  channel — the API always re-reads the database).

`app/collector/seed.py` bootstraps history: it runs the simulator's engine flat out (no OPC UA)
for a few simulated days and feeds the changes through the same tracker/store as a live
collector, then writes the simulator's own retentive state so the live `simulator` container
resumes exactly there. Idempotent (skipped if the database already has a `CELL` event); wired
into `docker-compose.yml` at day 8 so the first `docker compose up` starts with history already
in place and the simulation continuing live from that point.

## Data model (PostgreSQL + TimescaleDB)

- `state_events` — ts, scope (`CELL`/`OP10`/`OP20`/`OP30`), state, fault_code, stop_origin
  (CELL scope only). Unique (scope, ts).
- `stops` — id, start_ts, end_ts (null while open), duration_s, state, origin_station,
  fault_code, suggested_category, is_induced (generated: state is STARVED/BLOCKED),
  qualified_cause, qualified_at. Unique (start_ts); at most one open stop. There is no stored
  `is_micro`: the micro-stop threshold is configurable, so it is applied at read time on
  `duration_s`.
- `counter_samples` (hypertable) — ts, good_total, scrap_total, theoretical_cycle_s,
  last_cycle_s. Primary key (ts). The theoretical cycle time is stored with each sample so OEE
  stays exact if it changes; the last cycle time feeds the live view.
- `weld_samples` (hypertable) — ts, current_ka, points_since_cap_change, ok. Primary key (ts).
- `comm_gaps` — start_ts, end_ts (null while open). Unique (start_ts); at most one open gap.
- `collector_state` — single row: last_seen_ts (latest simulated time received, the UI's "now"),
  updated_at.

OEE is computed at read time from `stops`, `counter_samples` and `comm_gaps`; nothing is
pre-aggregated. All timestamps are simulated time (`timestamptz`, UTC, no DST). Production day =
05:00 → 05:00 (the first shift start), so the night shift is never cut at midnight.

`ACCELERATION` (root `.env`, read by the simulator and the collector through docker-compose) is
the simulation speed factor; it would be 1 on a real PLC. The heartbeat watchdog uses it:
timeout = max(3 s wall, 5 s simulated ÷ ACCELERATION).

Operator qualification causes: Mechanical breakdown, Electrical breakdown, Setup/adjustment,
Tool change, Missing parts, Downstream saturation, Quality issue, Other.
The system pre-fills `suggested_category` from the fault code; the operator confirms or changes it.

## API (FastAPI)

`from`/`to` query parameters are ISO 8601 with an explicit UTC offset; a naive datetime is
rejected (422). Timestamps in responses are ISO 8601 with a trailing `Z`, simulated time.

- `GET  /api/cell/live` — current state, counters, weld, open stop, OEE of the current shift
- `WS   /ws/live` — the same snapshot, pushed at most every 250 ms (`api.ws_publish_interval_ms`);
  OEE inside it is refreshed at most once a second (`api.oee_refresh_interval_s`), shared by every
  client. Fed by PostgreSQL `LISTEN/NOTIFY` on the `notify_channel` the collector notifies after
  each committed write — a wake-up hint only, never a data channel.
- `GET  /api/oee?from=&to=&group_by=shift|day` — OEE with A/P/Q breakdown
- `GET  /api/stops?unqualified=true&from=&to=` — non-planned stops of at least `oee.micro_stop_s`,
  the open one included once it reaches that duration
- `PATCH /api/stops/{id}` — body `{"cause": "..."}` (one of the 8 causes); 404 unknown id, 409 a
  planned stop or a micro-stop, 422 an unknown cause
- `GET  /api/stops/pareto?from=&to=&by=cause|station|fault_code&metric=duration|count` — a
  dedicated "unqualified" bucket by cause; a per-bucket unqualified share by station/fault code
- `GET  /api/weld?from=&to=` — weld current and scrap rate, bucketed in time (`time_bucket`-style,
  aligned on the shift day start), plus tip-dressing/cap-change markers
- `GET  /healthz` — liveness only (no DB query), the Docker healthcheck target

## Frontend (three screens, no more)

1. **Live cell view** — current state (colour-coded), parts, cycle time, OEE of the current shift
   broken down as Availability × Performance × Quality, updating in real time.
2. **Operator stop qualification** — tablet-first, large touch targets, list of unqualified stops
   with pre-filled suggestion, one tap to confirm or change.
3. **Analysis** — stop Pareto (by cause / station), OEE per shift and per day, weld current and
   scrap rate over time (the electrode wear curve).

## Running it

`docker compose up --build`. Ordering, via `depends_on`: `db` (healthy) → `seed` (must exit 0 —
`condition: service_completed_successfully`, so it gates the rest rather than racing them) →
`simulator` (healthy) → `collector` + `api` (healthy) → `frontend`. `backend/Dockerfile.seed`
builds from the repo root so the one-shot `seed` step can install both `simulator` and `backend`
and write straight through the collector's own tracker/store (see `app/collector/seed.py`).

The handoff from `seed` to the live `simulator`/`collector` always leaves exactly one short
`comm_gaps` row (container start-up time, a few seconds real, scaled by `ACCELERATION`) — this is
the documented "collector wasn't watching yet" rule above, not a bug; `e2e/smoke.spec.ts` asserts
it stays under a generous bound (minutes, not hours).

`playwright.config.ts` + `e2e/` at the repo root run the compose stack end to end
(`npm run test:e2e`): `global-setup.ts` does `docker compose up --build --wait`, the specs load
all three screens and check for console errors, and `global-teardown.ts` tears the stack down
(set `KEEP_STACK=1` to leave it running for inspection after a failure).

## Known limitations

- The Live screen's state timeline ("Historique du poste") is polled over REST
  (`GET /api/cell/timeline`), not pushed over the WebSocket like the rest of the view. At high
  `ACCELERATION` its "Maintenant" label therefore lags "Heure usine" by a few real seconds (e.g.
  2–4 s, i.e. 2–4 simulated minutes, at 60×). Deliberate choice, not to be "fixed" without asking.

## Out of scope — do not build

Authentication, multiple cells or sites, ERP/MES integration, AI features, drivers for other
PLC brands, i18n of the UI (UI is in French), mobile apps.

## Conventions

- Code, identifiers, comments, commits: **English**. UI labels: **French**.
- Python 3.12, type hints everywhere, `ruff` for lint/format, `pytest`.
- Frontend: TypeScript strict, Next.js App Router, ESLint + Prettier.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`…).
- Every service has a Dockerfile and a healthcheck.
- Before writing code for a new step, **propose a short plan and wait for approval**.
- Never change the OEE definitions or the simulation model without asking.

## Roadmap (10 working days)

1–2  Simulator + OPC UA address space, verified with UaExpert
3–4  OEE core (pure, tested) + collector + DB
5–7  API + three screens
8    docker compose, clean-machine test
9    README (bilingual), architecture diagram, GIF
10   Article (French), publication

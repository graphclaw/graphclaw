# GraphClaw Load Tests

Locust-based load tests for the GraphClaw API server.

## Prerequisites

```bash
pip install locust>=2.20.0
# or, using the project extras:
pip install "graphclaw[load-testing]"
```

## Running the Load Test

### Interactive (Locust Web UI)

```bash
locust -f tests/load/locustfile.py \
    --host=http://localhost:8000 \
    --users=1000 \
    --spawn-rate=50
```

Then open [http://localhost:8089](http://localhost:8089) in your browser.

### Headless (CI / scripted)

```bash
locust -f tests/load/locustfile.py \
    --host=http://localhost:8000 \
    --users=1000 \
    --spawn-rate=50 \
    --headless \
    --run-time=60s
```

The process exits with code `1` if any pass/fail threshold is violated.

### Scenario files

```bash
# Morning-briefing spike (500 users, high cadence):
locust -f tests/load/scenarios/spike_test.py \
    --host=http://localhost:8000 \
    --users=500 \
    --spawn-rate=100 \
    --headless \
    --run-time=30s

# A2A agent throughput (100 concurrent agents):
locust -f tests/load/scenarios/a2a_throughput.py \
    --host=http://localhost:8000 \
    --users=100 \
    --spawn-rate=20 \
    --headless \
    --run-time=60s
```

## Pass/Fail Thresholds (PRD Phase 5)

| Metric | Threshold |
|---|---|
| P99 latency | < 2 000 ms |
| Error rate | < 1 % |
| Throughput | > 100 req/s at 1 000 concurrent users |

These thresholds are enforced automatically at the end of every headless run
via the `check_thresholds` Locust event listener in `locustfile.py`.

## User Mix

| Class | Weight | Share | Think time | Behaviour |
|---|---|---|---|---|
| `GatewayUser` | 9 | ~90% | 1-3 s | Read-heavy: health, settings, approvals, skill search, MCP list, auth |
| `HeavyUser` | 1 | ~10% | 2-5 s | Write-heavy: PATCH settings, POST MCP servers, compliance export |

## Interpreting Results

### Key columns in the Locust web UI / CSV output

| Column | Meaning |
|---|---|
| **# Requests** | Total requests sent during the run |
| **# Fails** | Requests that returned a non-2xx response or timed out |
| **Median (ms)** | P50 response time |
| **95%ile (ms)** | P95 response time |
| **99%ile (ms)** | P99 response time — primary threshold metric |
| **Average (ms)** | Mean response time (skewed by outliers; use percentiles) |
| **Current RPS** | Live throughput; compare against the 100 req/s threshold |
| **Current Failures/s** | Real-time error rate |

### Typical failure modes

- **High P99 on `/api/v1/task-update`** — broker or graph write contention;
  check Redis queue depth and Postgres AGE write latency.
- **High error rate on `/app/v1/mcp/servers` (POST)** — graph constraint
  violations from duplicate names in load tests; use unique names per user.
- **Low throughput** — check `uvicorn` worker count (`--workers`) and
  connection pool sizes (`DB_POOL_SIZE`, `REDIS_MAX_CONNECTIONS`).

### Auth tokens

By default all users send `Bearer load-test-token`.  The server will return
401 responses which are recorded as failures.  To test with valid tokens,
set the `LOAD_TEST_TOKEN` environment variable before running Locust:

```bash
export LOAD_TEST_TOKEN="<valid-jwt>"
locust -f tests/load/locustfile.py ...
```

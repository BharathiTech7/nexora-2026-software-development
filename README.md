# NEXORA 2026 — Software Development

LPDG x RGM Innovation Challenge — Software Development Discipline

---

## 1. Overview

LPDG operates approximately 320 gateways, each of which can become offline,
disconnect frequently, or reboot unexpectedly. Field engineers can perform at
most **15 gateway visits per week**. The task is to rank which 15 gateways
should be visited each Monday and explain why each one was selected.

The challenge provides a working 3-sigma anomaly baseline (`baseline_3sigma.py`)
as the ranking reference. This project is the **software layer built around that
baseline**: a Web API that exposes ranking results, explains individual gateway
rankings, and allows the ranking to be recomputed on demand. It now also
includes a lightweight web dashboard for interacting with the existing FastAPI API.

The supplied baseline ranking logic has been kept **unchanged**. Our contribution
is the engineering layer: data loading, service abstraction, API design, error
handling, and testing.


---

## 🎥 Demo Recording

**Watch the complete NEXORA 2026 Software Development demo:**

[▶️ Watch Demo on YouTube](https://youtu.be/xu4Q_uJruWQ)

The recording demonstrates the dashboard, weekly rankings, gateway explanations,
rerun functionality, API documentation, automated tests, and final validation.

---

## 2. Solution Architecture

```
Browser
        |
        v
NEXORA Frontend Dashboard -- presentation layer; no business logic
        |
        v
frontend/server.py      -- proxy server keeping browser same-origin
        |
        v
FastAPI API             -- HTTP layer; translates service results to JSON responses
        |
        v
RankingService          -- orchestrates loading + ranking; stores results; exposes query methods
        |
        v
RankingStrategy / Baseline3SigmaRanking -- concrete implementation of the supplied 3-sigma logic
        |
        v
DataLoader              -- loads and validates parquet; caches in memory
        |
        v
Telemetry parquet files (data/telemetry/)
```

**Component responsibilities:**

| Component | Responsibility |
|---|---|
| `NEXORA Frontend Dashboard` | Lightweight presentation layer UI. Contains no business or ranking logic. |
| `frontend/server.py` | Serves the frontend and proxies API requests to FastAPI, maintaining same-origin communication. |
| `DataLoader` | Locates, loads, and validates the parquet telemetry. Caches in memory; reloads on demand. |
| `RankingStrategy` | Abstract base class. Defines `rank_week` and `build_predictions`. |
| `Baseline3SigmaRanking` | Implements `RankingStrategy` using the supplied 3-sigma methodology. |
| `RankingService` | Calls the loader and strategy; stores the resulting predictions; answers queries. |
| `FastAPI app` | HTTP boundary. Accepts requests, calls the service, converts domain exceptions to HTTP responses. |

`RankingStrategy` is the code-level seam for swappability. API handlers depend
only on `RankingService`, which depends on `RankingStrategy`. A different
ranking implementation can be wired in by substituting the concrete class in
`get_ranking_service()` without touching any API handler.

---

## 3. Project Structure

```
NEXORA-2026/
|
+-- frontend/
|   +-- index.html               # Frontend Dashboard UI
|   +-- style.css                # Styling for the dashboard
|   +-- app.js                   # UI interaction and API calls
|   +-- server.py                # Lightweight web server and API proxy
|
+-- src/
|   +-- api/
|   |   +-- __init__.py
|   |   +-- main.py              # FastAPI app, endpoints, response models, exception handlers
|   +-- data/
|   |   +-- __init__.py
|   |   +-- loader.py            # DataLoader: load, validate, cache telemetry
|   +-- ranking/
|   |   +-- __init__.py
|   |   +-- base.py              # RankingStrategy abstract interface
|   |   +-- baseline.py          # Baseline3SigmaRanking implementation
|   +-- services/
|       +-- __init__.py
|       +-- ranking_service.py   # RankingService + domain exceptions
|
+-- tests/
|   +-- __init__.py
|   +-- test_ranking.py          # Ranking strategy unit tests
|   +-- test_validation.py       # DataLoader and service unit tests
|   +-- test_api.py              # FastAPI endpoint tests
|   +-- test_edge_cases.py       # Error handling, edge cases, bug regression
|   +-- test_e2e.py              # End-to-end tests (real algorithm + real HTTP)
|
+-- baseline_3sigma.py           # Supplied challenge baseline -- NOT MODIFIED
+-- validate_submission.py       # Supplied challenge validator -- NOT MODIFIED
+-- predictions.csv              # Baseline output -- passes official validator
+-- requirements.txt
+-- README.md
+-- DECISIONS.md
+-- AI-USAGE.md
+-- .gitignore                   # data/ and 03-challenge-data/ excluded
```

---

## 4. Ranking Logic

The ranking follows the supplied `baseline_3sigma.py` exactly:

1. For each scored Monday in the evaluation window:
2. Take the **trailing 28 days** of telemetry strictly before that Monday per gateway.
3. Compute per-gateway **mean** and **standard deviation** across three metrics:
   - `offline_duration_sec`
   - `disconnection_cnt`
   - `reboot_cnt`
4. Examine the **trailing 7 days** of that 28-day window.
5. Flag any hour where any metric exceeds the gateway-specific mean by more than **3 standard deviations**.
6. Rank gateways by **total flagged-hour count** (descending).
7. Select the **top 15**.

`Baseline3SigmaRanking` reproduces this logic faithfully. When run against the
challenge dataset, its output is **100% identical** to the original baseline
script — same gateways, same ranks, same scores, same reasons. The algorithm
was not changed.

---

## 5. API

Start the API and Frontend (see [Section 9](#9-running-locally)):

```
python -m uvicorn src.api.main:app --reload
```

Interactive documentation is available at `http://localhost:8000/docs` once
the server is running.

---

### GET /health

Returns service health and whether rankings are ready to query.

```
GET /health
```

Example response:

```json
{
  "status": "healthy",
  "is_ranking_ready": true,
  "available_weeks": [
    "2026-02-02",
    "2026-02-09",
    "2026-03-23"
  ]
}
```

---

### GET /rankings/{week}

Returns the top 15 ranked gateways for the given week. `week` must be a date
in `YYYY-MM-DD` format.

```
GET /rankings/2026-03-23
```

Example response:

```json
{
  "week": "2026-03-23",
  "total_ranked": 15,
  "gateways": [
    {
      "week": "2026-03-23",
      "rank": 1,
      "gateway_id": "<gateway-id>",
"score": "<score>",
"reason": "<ranking explanation>"
    }
  ]
}
```

Error responses:
- `422` — malformed date string
- `404` — week not in scored results
- `503` — rankings have not been computed yet

---

### GET /gateways/{gateway_id}/explanation

Returns the ranking and explanation for a specific gateway in a specific week.
The required `week` query parameter must be in `YYYY-MM-DD` format.

`gateway_id` accepts two forms (case-insensitive):
- 12 hexadecimal characters
- Colon-separated MAC address

```
GET /gateways/{gateway_id}/explanation?week=2026-03-23
```

Example response:

```json
{
  "gateway_id": "<gateway-id>",
  "week": "2026-03-23",
  "rank": 1,
  "score": "<score>",
  "reason": "<ranking explanation>"
}
```

Error responses:
- `422` — malformed gateway ID or date, or missing `week` parameter
- `404` — gateway or week not found in results
- `503` — rankings have not been run

---

### POST /run

Reloads telemetry from disk and recomputes rankings.

This is how the system is told that updated telemetry data has been placed in
the `data/` directory. The endpoint reloads the parquet files, runs the
ranking strategy across all scored weeks, and stores the results in memory.

```
POST /run
```

Example response:

```json
{
  "status": "success",
  "message": "Ranking computation completed successfully.",
  "total_rows": 120,
  "weeks_count": 8,
  "available_weeks": [
    "2026-02-02",
    "2026-02-09",
    "2026-03-23"
  ]
}
```

Error responses:
- `503` — data directory missing or not found
- `500` — telemetry fails schema validation

---

## 6. Error Handling

All error responses share a consistent JSON structure:

```json
{
  "error": "ErrorCategory",
  "detail": "Human-readable description."
}
```

| Situation | HTTP Status | Error category |
|---|---|---|
| Rankings not yet computed | 503 | `RankingNotReady` |
| Requested week not in results | 404 | `WeekNotFound` |
| Requested gateway not in results | 404 | `GatewayNotFound` |
| Malformed date or gateway ID | 422 | `InvalidInput` |
| Data directory missing | 503 | `DataNotFound` |
| Telemetry schema invalid / empty / unreadable | 500 | `DataValidationError` |

No Python stack traces are exposed in error responses. Domain exceptions from
`RankingService` and `DataLoader` are translated to HTTP responses at the API
boundary. The service layer contains no FastAPI imports.

---

## 7. Testing

```
python -m pytest tests/ -q
```

**57 passed, 0 failed.**

| Test file | What it covers |
|---|---|
| `test_ranking.py` | `Baseline3SigmaRanking` unit tests: anomaly detection, empty window, schema, determinism |
| `test_validation.py` | `DataLoader` and `RankingService` unit tests: loading, schema validation, service state |
| `test_api.py` | FastAPI endpoint tests with synthetic data and a mock strategy |
| `test_edge_cases.py` | Bad input, missing data, unknown gateway/week, error propagation, stale-cache regression |
| `test_e2e.py` | End-to-end tests using the real ranking algorithm |

### End-to-end tests

Two E2E tests cross the complete real stack:

```
synthetic parquet -> DataLoader -> Baseline3SigmaRanking -> RankingService -> FastAPI HTTP
```

**`test_e2e_run_rank_explain_consistency`**  
Calls `POST /run`, then `GET /rankings/{week}`, then
`GET /gateways/{id}/explanation` and verifies the data returned across all
three responses is internally consistent — matching rank, score, week, and
reason fields.

**`test_e2e_run_twice_updates_results`**  
Calls `POST /run` with one dataset, replaces the telemetry on disk, calls
`POST /run` again, and verifies the new rankings reflect the new data rather
than the stale cache.

### Bug-driven regression test

**`test_bug_regression_stale_cache_after_failed_force_reload`**

Protects against a genuine bug found during development. When
`DataLoader.load_telemetry(force_reload=True)` failed (for example, an
operator placed a corrupted file in the data directory), the previous cached
DataFrame was not cleared. Subsequent non-forced calls silently returned stale
data instead of propagating the error. The fix clears `_cached_telemetry` to
`None` before any reload attempt. This test reproduces the failure scenario
and verifies the corrected behaviour.

No challenge data is used in any test. All tests use synthetic in-memory data
or controlled temporary directories.

---

## 8. Verification

Generate predictions using the supplied baseline:

```
python baseline_3sigma.py --data data --out predictions.csv
```

Output: `wrote predictions.csv — 120 rows over 8 weeks`

Run the official validator:

```
python validate_submission.py predictions.csv
```

Output:

```
predictions.csv: OK
  15 ranked gateways for each of 8 weeks, 2026-02-02 to 2026-03-23
```

When `Baseline3SigmaRanking` is run through `RankingService` against the
challenge dataset, its output is 100% identical to the original
`baseline_3sigma.py` output — same gateways, same ranks, same scores, same
reasons, in the same row order.

---

## 9. Running Locally

Prerequisites: Python 3.10 or later, pip.

```bash
# Install dependencies
pip install -r requirements.txt

# Generate predictions using the supplied baseline
python baseline_3sigma.py --data data --out predictions.csv

# Validate the predictions
python validate_submission.py predictions.csv

# Run the test suite
python -m pytest tests/ -q
```

To start the backend and frontend:

**Terminal 1 (Backend API):**
```bash
python -m uvicorn src.api.main:app --reload
```

**Terminal 2 (Frontend Dashboard):**
```bash
python frontend/server.py
```

Then open your browser to the Frontend Dashboard:
`http://127.0.0.1:5500`

The FastAPI API is available directly at:
`http://127.0.0.1:8000`

The server attempts to compute rankings from the `data/` directory at startup.
If the data directory is not yet present, the server starts cleanly and
rankings become available after calling `POST /run`.

All paths are resolved relative to the repository root. No machine-specific
paths are hardcoded.

---

## 10. API Documentation

FastAPI generates interactive documentation automatically.

With the server running, visit:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

These pages document all endpoints, request and response schemas, and error
responses. No additional setup is required.

---

## 11. Frontend Dashboard

The project includes a lightweight presentation layer that runs in the browser. It communicates with the existing FastAPI backend through `frontend/server.py`, which serves the static files and proxies API requests. This proxy keeps the browser-to-server communication same-origin while preserving the existing FastAPI backend unchanged. 

The dashboard provides the following UI capabilities:
- API health and status monitoring
- Weekly gateway rankings display
- Top 15 gateways visualization
- Gateway ranking explanations
- Run Now / recompute rankings functionality
- Prediction CSV download
- Direct navigation to API Docs
- Robust loading and error states

The frontend contains **no business or ranking logic**—all logic remains entirely in the existing backend.

---

## 12. Design Notes

**Baseline kept unchanged.** The supplied `baseline_3sigma.py` is the
reference ranking implementation. Reproducing its output exactly and building
a reliable engineering layer around it was the goal of the Software Development
discipline. The algorithm was not modified.

**Computation kept simple.** Rankings are computed in memory when `POST /run`
is called and cached until the next run. There is no database, job queue, or
background worker. This is appropriate for a team running a single ranking per
week.

**No authentication or multi-tenancy.** The challenge specifies a single
operations team making a handful of API requests per day. Authentication and
multi-user support were not added.

**No telemetry upload endpoint.** New data is placed in the mounted `data/`
directory and then `POST /run` is called. Uploading large parquet files over
HTTP was deliberately not implemented.

**Swappable ranking.** `RankingStrategy` is a Python abstract base class. To
replace the ranking algorithm, implement a new subclass and update the single
reference in `get_ranking_service()` in `src/api/main.py`. No API handler
needs to change.

---

## 13. Limitations

- **Does not improve the anomaly detection algorithm.** The ranking method is
  the supplied 3-sigma baseline.
- **Does not authenticate requests.** There is no API key, token, or session
  management.
- **Does not support multiple users or tenants.** It is a single-process
  application.
- **Does not queue or schedule ranking runs.** Computation is synchronous when
  `POST /run` is called.
- **Does not accept data uploads.** Telemetry must be placed in
  `data/telemetry/` in the expected parquet layout before calling `/run`.
- **Requires the expected telemetry schema.** The loader validates for
  `gateway_id`, `ts_utc`, `offline_duration_sec`, `disconnection_cnt`, and
  `reboot_cnt`. Files with different schemas are rejected with a clear error.
- **In-memory state.** Rankings reset if the server restarts. Calling
  `POST /run` after restart restores them.

---

## 14. Challenge Data

The challenge telemetry dataset is confidential and **not included in this
repository**.

The `.gitignore` explicitly excludes:
- `data/`
- `03-challenge-data/`
- `*.zip`

To use this project with the challenge data, unzip it into the `data/`
directory at the repository root, then call `POST /run` or run
`baseline_3sigma.py` directly.

No telemetry rows, gateway IDs, or dataset samples appear in source code,
tests, or documentation.

---

## 15. Development Notes

The implementation was developed incrementally across phases: project
structure, ranking abstraction, data loading, service layer, API, error
handling, testing, and final verification. Automated tests were written
alongside each component.

AI assistance was used during development. Details of what was assisted, what
was manually verified, and one error that was caught and corrected are
documented in `AI-USAGE.md`.

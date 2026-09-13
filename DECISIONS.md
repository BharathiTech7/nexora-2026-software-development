# DECISIONS

This document records the five key engineering decisions made for the NEXORA 2026
Software Development submission. Each decision describes what was chosen, what the
primary alternative was, why the choice was made, and what was traded away.

---

## 1. Selected Software Development

### Decision

We selected Software Development as the primary discipline for this challenge submission.

### Alternative

Select a different discipline: Data Science, ML, Data Engineering, DevOps, or MLOps.

### Why

Our strongest relevant contribution for this challenge is building reliable software
around the supplied ranking baseline. The work undertaken — a data loading layer,
a swappable ranking interface, a service layer, a Web API with three functional
endpoints, deliberate error handling, and an automated test suite of 57 tests
including two end-to-end tests — maps directly to what the Software Development
discipline evaluates. The challenge's stated Software Development requirements
(swappable ranking, API with specific capabilities, deliberate input and error
handling) align with the work we implemented.

### Trade-off

We do not spend the majority of solution effort improving the anomaly-detection
algorithm, building a machine-learning model, or optimising the data pipeline.
The trade-off is intentional: the chosen discipline is evaluated primarily on
software engineering quality, not algorithmic innovation.

---

## 2. Keep the Supplied 3-Sigma Baseline Unchanged

### Decision

Keep `baseline_3sigma.py` unchanged and build the software system around its
ranking logic rather than modifying or replacing it.

### Alternative

Modify the ranking algorithm or replace it with a different anomaly-detection or
scoring approach (e.g. adjusted thresholds, alternative metrics, or an ML model).

### Why

The supplied baseline is explicitly usable for the Software Development track.
Keeping it unchanged gives us a stable, verifiable reference: we can confirm that
`Baseline3SigmaRanking` and `RankingService` produce output that is 100% identical
to running `baseline_3sigma.py` directly — row by row, value by value. This parity
is confirmed both by automated tests using synthetic data and by comparison against
the real challenge dataset. The official validator (`validate_submission.py`) also
passes without modification.

### Trade-off

We make no attempt to improve ranking quality by changing the algorithm. This limits
algorithmic innovation, but it reduces the risk of introducing errors, keeps the
Software Development work focused and verifiable, and preserves the original
baseline as an unambiguous reference throughout development.

---

## 3. Use a RankingStrategy Abstraction

### Decision

Define a `RankingStrategy` abstract base class and make `RankingService` depend on
that abstraction rather than directly on `Baseline3SigmaRanking`.

### Alternative

Have `RankingService` directly instantiate and call `Baseline3SigmaRanking` with no
intermediate interface. This would reduce the number of files and levels of
indirection.

### Why

The challenge explicitly requires the ranking logic to be swappable without changing
the API or service handlers. A small abstract interface (`base.py`, ~25 lines)
provides a clear code-level seam: API handlers depend only on `RankingService`,
`RankingService` depends only on `RankingStrategy`, and `Baseline3SigmaRanking` is
one implementation of that interface. A second ranking implementation can be wired
in by changing one reference in `get_ranking_service()` in `src/api/main.py`. No
handler code changes. This is verified in tests using `DummyMockStrategy` and
`SyntheticMockStrategy`, which substitute freely into the same service and API
without modification.

### Trade-off

There is a small amount of additional abstraction compared with calling the baseline
directly. For this project, that cost is justified because it directly satisfies
the swappability requirement and is confirmed by the existing test fixtures, which
rely on the seam to substitute mock implementations.

---

## 4. Compute Rankings on Run and Keep Results In Memory

### Decision

Use `POST /run` to reload telemetry from disk, recompute rankings synchronously,
and keep the resulting predictions in process memory for subsequent API reads until
the next run.

### Alternative

Store computed rankings in a persistent database, write results to disk on each
run, or implement a background job queue so that `POST /run` is non-blocking.

### Why

The challenge describes one operations team making a small number of API requests
per day and running the ranking approximately once per week when new telemetry
arrives. Synchronous computation with in-memory caching is sufficient for that
workload, avoids an external database dependency, and makes the system behaviour
straightforward to understand and verify. `POST /run` reloads the parquet files
from disk each time it is called, which satisfies the "run again" requirement
(confirmed by `test_e2e_run_twice_updates_results`) without adding a job
coordination layer.

### Trade-off

Results are process-local: if the server restarts, rankings must be recomputed by
calling `POST /run`. There is also no distributed coordination if the application
were ever run as multiple processes. These are acceptable limitations for the stated
workload of one team, one process, one run per week. Adding durability or job
queuing would introduce complexity not justified by the challenge requirements.

---

## 5. Validate at the Data/API Boundaries and Test Failure Paths

### Decision

Perform explicit validation at the data-loading and API boundaries and write tests
that cover failure scenarios, including a regression test for a genuine cache-state
bug discovered during development.

### Alternative

Rely on unhandled downstream exceptions to surface errors, or restrict tests to
the happy path only.

### Why

The challenge requirements explicitly call for deliberate handling of bad input,
missing data, and unknown gateways. We implemented a layered approach: `DataLoader`
validates the parquet schema and raises typed exceptions (`DataNotFoundError`,
`DataValidationError`); `RankingService` validates gateway IDs and date formats and
raises typed domain exceptions; FastAPI exception handlers translate these to
structured JSON responses with consistent `error` and `detail` fields and
appropriate HTTP status codes. During development, testing revealed a genuine
cache-state bug: when `DataLoader.load_telemetry(force_reload=True)` failed (for
example, because a corrupted file had been placed in the data directory), the
previous `_cached_telemetry` value was not cleared before the reload attempt.
Subsequent non-forced calls silently returned the old cached data instead of
propagating the failure. The fix — clearing `_cached_telemetry` to `None` before
the reload attempt — is protected by the regression test
`test_bug_regression_stale_cache_after_failed_force_reload`, which reproduces the
original failure scenario and verifies the corrected behaviour.

### Trade-off

There is additional validation code across `loader.py`, `ranking_service.py`, and
`main.py`, and a larger test suite (57 tests across 5 files). This increases
implementation and maintenance effort compared with a minimal happy-path
implementation. The benefit is that failures are explicit, typed, and testable,
and the discovered bug has a permanent regression guard.

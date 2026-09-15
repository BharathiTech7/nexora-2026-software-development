# AI USAGE

This document describes how AI coding assistance was used during the development
of the NEXORA 2026 Software Development submission, including an example of an
AI-generated reasoning mistake that was caught and corrected.

---

## 1. Tools Used

AI coding assistance was used throughout the development process.

The primary uses were:

- **Architecture discussion** — talking through the layered design (DataLoader,
  RankingStrategy, Baseline3SigmaRanking, RankingService, FastAPI) and the
  rationale for each boundary.
- **Phase planning** — breaking the work into sequential implementation phases
  and identifying the ordering of dependencies.
- **Code generation and review** — generating and reviewing code for the data
  loading layer, the ranking abstraction and concrete implementation, the service
  layer, the FastAPI application and its exception handlers, and the test suite.
- **Edge case analysis** — identifying scenarios such as missing data directories,
  malformed timestamps, empty datasets, unknown gateways, unavailable weeks, and
  the cache-state behaviour during a failed reload.
- **Frontend architecture & integration** — UI implementation, diagnosing browser
  CORS issues, and building the lightweight standard-library proxy `frontend/server.py`
  to keep the backend unchanged.
- **Test failure diagnosis** — helping interpret test failure output and reason
  about what the failure indicated.
- **Documentation** — drafting README.md, DECISIONS.md, and this document.

---

## 2. How AI Was Used

AI assistance was used as a development aid rather than as an unquestioned source
of truth.

Each significant piece of generated code or reasoning was reviewed against:

- the NEXORA challenge requirements and the Software Development discipline criteria,
- the provided `baseline_3sigma.py` and `validate_submission.py` as authoritative
  references,
- actual test results from running the test suite,
- the output of the official `validate_submission.py` validator,
- and the observable behaviour of the running API.

Where AI-generated code or reasoning appeared incorrect or insufficient, it was
reviewed, corrected, or replaced before being retained.

---

## 3. Example of an AI Mistake We Caught

During Phase 5 (error handling and robustness), the AI-generated commentary
suggested that a threading lock was not necessary for the shared `_predictions`
state in `RankingService` because Python's GIL would prevent true parallel
corruption.

**This reasoning was incorrect.**

The GIL does not guarantee logical race-safety for application state. Two
concurrent requests — for example, `POST /run` and `GET /rankings/{week}` arriving
simultaneously — could observe inconsistent intermediate state even with the GIL
present, depending on how and when the Python interpreter switches between threads.
The GIL is an implementation detail of CPython that prevents simultaneous execution
of Python bytecode, not a mechanism that makes shared mutable application state
safe under concurrent access.

The GIL-based justification was caught during review and removed. It does not
appear in the final codebase, the DECISIONS.md, or the README.

**The actual justification in the final design is simpler and accurate:** the
challenge describes a single operations team making a handful of API requests per
day and running the ranking approximately once per week. The current design is
intentionally sized for that stated workload. If genuinely concurrent execution
became a real requirement — for instance, if the API were expected to serve
multiple simultaneous requests with ongoing background recomputation — that would
call for explicit synchronisation or a different execution model, and should be
addressed at that point with the actual requirement in view.

No threading lock was added. The design is not claimed to be concurrency-safe
beyond what the stated workload requires.

---

## 4. Human Verification

Generated code was verified through actual execution rather than accepted on the
basis of the generation alone. Specific verification steps carried out:

- **57/57 automated tests pass** across unit, integration, and end-to-end tests.
- **Two E2E tests** cross the real HTTP/API boundary using the actual
  `Baseline3SigmaRanking` algorithm and synthetic parquet telemetry, confirming
  that `POST /run` → `GET /rankings/{week}` → `GET /gateways/{id}/explanation`
  works end-to-end and returns internally consistent results.
- **Genuine bug found and fixed during testing.** The stale-cache bug
  (`DataLoader` retaining old `_cached_telemetry` after a failed `force_reload`)
  was discovered by running tests, not by AI analysis alone. The fix was verified
  by a regression test that reproduces the original failure scenario.
- **100% baseline parity confirmed.** `Baseline3SigmaRanking` run through
  `RankingService` produces output identical to `baseline_3sigma.py` run directly,
  verified row-by-row against the real challenge dataset.
- **Official validator passes.** `python validate_submission.py predictions.csv`
  returns `predictions.csv: OK`.
- **API endpoints manually verified** against expected behaviour: valid requests,
  malformed dates, unknown weeks, unknown gateways, missing parameters, and
  pre-run state.
- **Frontend manually verified** against API behavior and predictions.csv validity.
- **Backend remained completely locked** during UI development.
- **Git safety verified.** `data/`, `03-challenge-data/`, and `*.zip` are excluded
  by `.gitignore` and confirmed absent from `git status`.

---

## 5. Responsibility

Final responsibility for all aspects of this submission rests with the participant,
including:

- implementation choices and their trade-offs,
- the correctness of the code,
- the design and coverage of the test suite,
- compliance with the challenge requirements,
- the accuracy of all documentation,
- and the submission itself.

AI assistance accelerated development and helped identify issues, but all
decisions were reviewed, verified through execution, and accepted or rejected
by the participant before being included in the submission.

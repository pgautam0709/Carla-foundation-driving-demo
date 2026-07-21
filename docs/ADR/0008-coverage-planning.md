# ADR 0008 — Coverage Planning

**Date:** 2026-07-20
**Status:** Proposed
**Deciders:** AI Engineering Team

---

## Context

`stats.json` already reports `towns: dict[str, int]` — how many included
episodes came from each town. Nothing today says whether that is *enough*,
or which combinations of conditions (town × weather, in this phase) are
missing entirely. Without an explicit target, "diversity" has no
definition a tool can check, and future data-collection effort is
undirected. This ADR defines `src/quality/coverage.py`, which both
`scoring.py`'s `coverage` metric (ADR-0005) and `review.py`'s
recommendations consume.

---

## Decisions

### 1. Coverage is defined over a configured target matrix of (town × weather) cells, not inferred from the data itself

**Decision:** `config/default.yaml` gains:

```yaml
quality_engineering:
  coverage:
    target_towns: ["Town01", "Town02", "Town03", "Town04", "Town05", "Town10"]
    target_weather: ["ClearNoon", "CloudyNoon", "WetNoon", "HardRainNoon", "ClearSunset", "ClearNight"]
    min_episodes_per_cell: 3
    max_recommendations: 5
```

A "cell" is one `(town, weather)` pair. The full target matrix is the
Cartesian product of `target_towns × target_weather`
(36 cells with the defaults above). `compute_coverage()` counts included
episodes per cell from `episodes_index.jsonl` (using the `town` and
`weather` fields — `weather` added per ADR-0004 Decision 6) and reports,
per cell, `episode_count` and `met: episode_count >= min_episodes_per_cell`.

**Rationale:**
- Coverage is inherently a question of "enough of what we *want*," not
  "enough of what we *have*" — a target must be declared somewhere, and
  config is the only place in this repository that owns intent versus
  observation (AGENTS.md §3). Inferring targets from the data itself (e.g.
  "target = whatever towns have ever been collected") would make coverage
  score 100% by definition the moment collection stops varying, which
  defeats the purpose.
- A town × weather matrix is deliberately the simplest target shape that
  still matches the brief's own example ("Collect additional rainy Town10
  episodes" — a town/weather combination, not a town alone or a weather
  alone). Routes are tracked in the report (Decision 4) but deliberately
  left out of the *target matrix* itself — see Rejected Alternatives.
- `min_episodes_per_cell` (not just "present vs. absent") avoids treating
  one lucky episode as "coverage achieved" — a cell with exactly one short
  episode is barely more useful for training diversity than an empty one.

**Rejected alternative — a three-dimensional town × weather × route
matrix:** rejected for this phase because `route_name` is a free-text
label chosen at collection time (see `expert_collection.route` in
`config/default.yaml`) with no fixed enumerable set the way towns and
CARLA weather presets have — a route-inclusive matrix would need route
*names* to be configured as a closed list too, which is a bigger config
surface change than this phase's scope justifies. Route diversity is
still reported (Decision 4) as an informational dimension; promoting it
into the target matrix is a natural, isolated follow-up (a config-only
change, no code change to `coverage.py`'s cell logic) once route naming is
itself standardized.

**Consequences:** `coverage` (ADR-0005's metric) is `100 * cells_met /
len(target_towns) * len(target_weather)` — directly derived from this
matrix, so retuning the coverage target (e.g. dropping a town this project
no longer cares about) is a one-line YAML edit with no code change.

### 2. Gap ranking maximizes marginal diversity gain, and is fully deterministic

**Decision:** `recommend_collection(coverage_result, cfg) ->
list[CoverageRecommendation]` ranks unmet cells by, in order:

1. **Zero-episode cells before under-threshold cells** — a cell with 0
   episodes ranks above a cell with 1 out of 3 required episodes.
2. **Fewer existing episodes first** — among cells with the same
   zero/non-zero status, the cell with the smallest `episode_count` ranks
   first.
3. **Town name, then weather name, alphabetically** — the final,
   deterministic tiebreaker.

The top `max_recommendations` (config) entries are returned.

**Rationale:**
- The brief requires recommendations that "maximize diversity" and are
  "never random." Prioritizing zero-coverage cells over partially-covered
  ones directly maximizes the *number of distinct conditions* represented
  in the dataset per additional episode collected — the textbook
  definition of maximizing diversity under a fixed collection budget,
  computed with simple sorting (no optimization solver, no randomness,
  consistent with every other Phase 3.5 module's dependency-light design).
- An alphabetical final tiebreaker (rather than, say, dict iteration order)
  is what makes two runs against the same `episodes_index.jsonl` produce
  byte-identical recommendation lists — required for the hash-consistency
  and golden-output tests in the testing section, and consistent with
  `dataset_splits.py`'s own insistence on deterministic ordering
  (ADR-0002 Decision 2).

**Consequences:** `recommend_collection()` never needs a random seed
parameter at all — unlike `dataset_splits.assign_splits()`, which
explicitly seeds its hash-based ordering, coverage ranking has no
legitimate reason to vary run-to-run given the same inputs, so no seed
parameter exists to be misused.

### 3. Recommendations are read-only advice; nothing in this phase can trigger a collection run

**Decision:** `recommend_collection()` returns data
(`CoverageRecommendation` records: town, weather, current episode count,
gap size, human-readable message like `"Collect additional rainy Town10
episodes (0/3 episodes)"`). It never invokes
`scripts/collect_expert_episode.py` or any CARLA-dependent code.

**Rationale:** Directly required by the Non-Goals section (no evaluation
driving, no Phase 4+ scope creep) and ADR-0004 Decision 3 (Phase 3.5 has no
side effects on the world, only on its own output files). Automating
"recommend → collect" into one command is a legitimate future Phase 4+
convenience but is explicitly out of scope here — it would also require
this dependency-light package to gain a CARLA dependency, which nothing
else in `src/quality/` needs.

**Consequences:** `make recommend-data`'s output is designed to be directly
actionable by a human running `collect_expert_episode.py` by hand (it
prints exactly the `--town` / weather values to use), rather than
attempting to script the handoff.

### 4. Route and split-balance diversity are reported but not gated

**Decision:** `compute_coverage()` also reports, informationally
(no `met` verdict, no target):
- Distinct `route_name` values represented, with per-route episode counts
  (from `episodes_index.jsonl`).
- Per-split (`train`/`val`/`test`) town/weather coverage, so a reviewer can
  see if diversity that exists in the dataset overall is actually present
  in the `train` split specifically.

**Rationale:** These are useful diagnostic signals (a route that only ever
lands in `test` is a real problem for a future trainer) but do not have a
declared target the way town/weather do (Decision 1's rejected
alternative) — reporting them without gating avoids implying a false
precision ("route coverage: 40%" would need a route target that doesn't
exist yet).

**Consequences:** `review.py`'s weaknesses list can still surface
"route X only appears in `test`" as a qualitative observation even though
no numeric coverage score depends on it — deterministic rule-based text,
not a fabricated metric.

### 5. `CoverageResult` and `CoverageRecommendation` are dataset-scoped, not lineage-scoped

**Decision:** Coverage is always computed against one dataset's
`episodes_index.jsonl` in isolation — it does not accumulate history
across previous dataset versions.

**Rationale:** Coverage is a property of "what is in this dataset right
now," which is exactly what a reviewer deciding "is this build ready to
train on" needs (feeds `gates.py`, ADR-0004). Trends in coverage *over
time* (is coverage improving release to release) are a `regression.py`
concern (ADR-0007 already compares `stats.json:towns`/weather across
datasets) and a `dashboard.py` trend concern (ADR-0009) — not duplicated
here.

**Consequences:** Running `make recommend-data` against an old dataset
still gives a correct, self-contained answer for that dataset, without
needing to know what came before it.

# Phase 3.5 — Architecture Decision Index

Phase 3.5's architecture went through two rounds of review
(`docs/PHASE3_5_DESIGN_REVIEW.md`, `docs/PHASE3_5_ARCHITECTURE_REVISION.md`)
before implementation began. This document indexes the resulting ADRs and
records the final scope decision that governed implementation.

## ADR index

| ADR | Title | Decides |
|-----|-------|---------|
| [0004](ADR/0004-engineering-loop-architecture.md) | Engineering loop architecture | Package boundaries, one-way dependency (`src/quality/` reads Phase 3 output, never the reverse), the `CategoryRegistry` pattern |
| [0005](ADR/0005-quality-scoring-strategy.md) | Quality scoring strategy | Weighted-mean (not minimum) metric combination, grade thresholds, star-rating reuse of grade thresholds |
| [0006](ADR/0006-artifact-versioning.md) | Artifact versioning | `VersionRecord` shape, config-hash scope, `previous_artifact_id` resolution by mtime |
| [0007](ADR/0007-regression-detection.md) | Regression detection | Artifact-agnostic snapshot comparison core, severity thresholds |
| [0008](ADR/0008-coverage-planning.md) | Coverage planning | Town×weather target matrix, deterministic recommendation ranking |
| [0009](ADR/0009-engineering-dashboard.md) | Engineering dashboard | Static self-contained HTML, pluggable section registry, no charting library dependency |
| [0010](ADR/0010-future-ml-integration.md) | Future ML integration | The pattern Phase 4 follows to add a `model` artifact type without modifying Phase 3.5 code |
| [0011](ADR/0011-experiment-tracking-lineage.md) | Experiment tracking / lineage | Cross-artifact-type derivation graph, distinct from same-type version history |

ADRs 0001-0003 predate Phase 3.5 (project scaffold, dataset engineering,
dataset hardening) and are unchanged by this phase.

## Final scope decision

After the second review round, the architecture was accepted **with
exactly one implementation note**: `lineage.py`'s possible future
relocation to a shared-infrastructure location outside `src/quality/`,
should Phase 4 need lineage tracking for a second artifact type (recorded
in ADR-0011 and `docs/DATASET_VERSIONING.md`). No restructuring was done
on the strength of this note — it is a flag for Phase 4 to revisit, not a
Phase 3.5 action item.

Three further improvements were proposed during review and **explicitly
not implemented** in Phase 3.5, by product-owner decision rather than
oversight:

1. **An event system** (`events.py`, a pub/sub-style `Event` abstraction
   decoupling e.g. "dataset versioned" from "changelog generated"). The
   current direct function-call chain (`write_version_artifacts()` calls
   `generate_changelog()` calls `compare_datasets()`, etc.) was judged
   sufficiently simple and traceable for six pipeline stages; an event
   bus would add indirection without a corresponding need.
2. **A `Pipeline` abstraction** generalizing the six `make <verb>`
   targets into a composable stage-runner. `quality-loop-dry-run` already
   composes the stages at the Makefile level; a code-level `Pipeline`
   class was judged premature until a second, meaningfully different
   pipeline exists to prove the abstraction out.
3. **Typed enums replacing string categories** (e.g. `artifact_type` as
   an enum rather than a plain string). Every category value in this
   codebase remains a plain string — `artifact_type="dataset"`,
   `category="dataset"` in both `CategoryRegistry` instances, severity
   values (`"failure"`/`"warning"`/`"improvement"`/`"informational"`) —
   consistent with how Phase 3's own schemas already represent categories
   (e.g. `split: str` rather than a `Split` enum), and avoiding an enum
   surface that Phase 4's second artifact type would immediately need to
   extend.

These three remain available to reconsider in a future phase if a
concrete need emerges (e.g. a second event consumer, a second pipeline
shape, or cross-language schema sharing that would benefit from typed
enums) — they were deferred as unneeded complexity for six pipeline
stages and one artifact type, not rejected as bad ideas in the abstract.

## Non-goals (reaffirmed)

Consistent with the original project brief and `docs/PHASES.md`, Phase
3.5 added no Behavioural Cloning, PyTorch, training loop, reinforcement
learning, diffusion, VLA, or model-evaluation code. `src/quality/` reads
Phase 3's output and writes its own new files; nothing it produces is
consumed by a training loop yet, since none exists in this repository.

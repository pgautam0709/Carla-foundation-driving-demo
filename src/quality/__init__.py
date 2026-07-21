"""
src/quality — Phase 3.5 engineering loop: score, version, compare, plan, and
dashboard the artifacts Phase 3 (and, later, Phase 4-6) produce.

See docs/ADR/0004-engineering-loop-architecture.md for the package design
and docs/ENGINEERING_LOOPS.md for the end-to-end flow.

This package only reads artifacts other phases already write to disk and
writes its own new files alongside them — it never mutates another
phase's output, and no other phase depends on it (see ADR-0004
Decision 1's one-way dependency).
"""

from __future__ import annotations

#: Version of the src.quality package itself, independent of
#: project.version in config/default.yaml (see ADR-0006 Decision 1).
__version__: str = "0.1.0"

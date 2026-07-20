"""
src/data/dataset_duplicates.py — Exact duplicate frame detection.

Hashes every included sample's frame file and groups samples that share
byte-for-byte identical content. Two ticks — within one episode or across
different episodes — with identical frames usually indicate a collection
issue (e.g. a frozen camera, or the same source frame written twice) rather
than coincidence, since raw camera frames have very high entropy.

Only exact (byte-for-byte) duplicates are detected here. Near-duplicate
(perceptually similar but not identical) detection would require decoding
and comparing pixel data, which needs an image library (Pillow/OpenCV) not
present in the base dependency set — see docs/PHASE3B_DATASET_HARDENING.md
for why that trade-off was made.

Future enhancement: perceptual near-duplicate detection can be added later
using image hashing (e.g. average/difference hash) or embeddings, but
Phase 3b intentionally avoids image-processing dependencies.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from typing import Any

from src.data.dataset_schemas import SampleRecord


@dataclasses.dataclass
class DuplicateGroup:
    """A set of samples whose frame files are byte-for-byte identical.

    Args:
        content_hash: First 16 hex characters of the SHA-256 digest of the
            shared frame content.
        sample_ids: Every sample_id sharing this frame content, in the
            order the samples were given.
        episode_ids: Distinct episode_ids represented in ``sample_ids``,
            sorted. A single-element list means the duplicate is confined
            to one episode (e.g. a frozen camera); multiple elements mean
            the duplicate spans episodes.
    """

    content_hash: str
    sample_ids: list[str]
    episode_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


def find_duplicate_frames(samples: list[SampleRecord]) -> list[DuplicateGroup]:
    """Group samples whose frame files are byte-for-byte identical.

    Args:
        samples: Samples to check — typically every sample from included
            episodes in one dataset build.

    Returns:
        One :class:`DuplicateGroup` per content hash shared by 2 or more
        samples, ordered by first appearance. Samples whose frame file is
        missing or unreadable are silently skipped (already reported
        elsewhere by alignment/validation checks).
    """
    by_hash: dict[str, list[SampleRecord]] = {}
    order: list[str] = []
    for sample in samples:
        try:
            digest = hashlib.sha256(Path(sample.frame_path).read_bytes()).hexdigest()[:16]
        except OSError:
            continue
        if digest not in by_hash:
            order.append(digest)
        by_hash.setdefault(digest, []).append(sample)

    groups: list[DuplicateGroup] = []
    for digest in order:
        members = by_hash[digest]
        if len(members) < 2:
            continue
        groups.append(DuplicateGroup(
            content_hash=digest,
            sample_ids=[m.sample_id for m in members],
            episode_ids=sorted({m.episode_id for m in members}),
        ))
    return groups

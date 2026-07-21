"""
src/quality/registry.py — Generic category-based registry.

One implementation, reused by :mod:`src.quality.metrics` (``METRIC_REGISTRY``)
and :mod:`src.quality.dashboard` (``SECTION_REGISTRY``) — see
docs/ADR/0004-engineering-loop-architecture.md Decision 6b. Neither of those
two consumers' modules is imported here; ``CategoryRegistry`` knows nothing
about metrics or dashboard sections, only that items are named and grouped
by an arbitrary category string.

A future third consumer (not currently needed) reuses this class a third
time rather than writing a third near-identical registry.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar


class _Named(Protocol):
    """Structural contract: anything registered must have a stable ``name``."""

    name: str


T = TypeVar("T", bound=_Named)


class CategoryRegistry(Generic[T]):
    """Register and query items of type ``T``, grouped by category string.

    Usage::

        METRIC_REGISTRY: CategoryRegistry[Metric] = CategoryRegistry()
        METRIC_REGISTRY.register("dataset", SynchronizationMetric())
        METRIC_REGISTRY.all("dataset")  # -> [SynchronizationMetric(), ...]
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, T]] = {}

    def register(self, category: str, item: T) -> None:
        """Register *item* under *category*, keyed by ``item.name``.

        Args:
            category: Arbitrary grouping string, e.g. ``"dataset"``.
            item: The item to register. Must have a unique ``name`` within
                *category*.

        Raises:
            ValueError: If an item with the same ``name`` is already
                registered under *category*.
        """
        bucket = self._items.setdefault(category, {})
        if item.name in bucket:
            raise ValueError(
                f"{item.name!r} is already registered under category {category!r}"
            )
        bucket[item.name] = item

    def get(self, category: str, name: str) -> T:
        """Return the item registered as *name* under *category*.

        Args:
            category: The category to look in.
            name: The item's registered name.

        Returns:
            The registered item.

        Raises:
            KeyError: If no such item is registered.
        """
        try:
            return self._items[category][name]
        except KeyError as exc:
            raise KeyError(
                f"No item named {name!r} registered under category {category!r}"
            ) from exc

    def all(self, category: str | None = None) -> list[T]:
        """Return every registered item, optionally filtered to one category.

        Args:
            category: If given, only items registered under this category.
                If None, every item across every category.

        Returns:
            List of items in registration order within each category.
        """
        if category is None:
            return [item for bucket in self._items.values() for item in bucket.values()]
        return list(self._items.get(category, {}).values())

    def categories(self) -> list[str]:
        """Return every category with at least one registered item, sorted."""
        return sorted(self._items)

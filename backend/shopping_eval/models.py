"""Contracts for the shopping search evaluation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckResult:
    """Result of a single deterministic check on one search result."""

    url: str
    link_loads: bool | None = None          # Check 1: HTTP HEAD returned 200
    product_matches: bool | None = None     # Check 2: product at URL matches intended item
    dimension_matches: bool | None = None   # Check 3: dimensions within room constraints
    match_detail: str = ""                  # Why product_matches passed/failed
    dimension_detail: str = ""              # Why dimension_matches passed/failed


@dataclass(frozen=True)
class TrialResult:
    """Outcome of running one query variant for one item through Exa + checks."""

    query: str
    query_components: list[str]             # Which components built this query
    search_type: str                        # "auto", "deep", "keyword"
    results_count: int
    check_results: list[CheckResult]
    link_alive_rate: float                  # Fraction of results where link loads
    product_match_rate: float               # Fraction where product actually matches
    dimension_match_rate: float             # Fraction where dimensions match (or N/A)
    latency_ms: int = 0


@dataclass
class BenchmarkCase:
    """A single test case: one extracted item + context for searching."""

    item_id: str                            # Unique ID for this test case
    item: dict                              # Extracted item dict (category, description, etc.)
    design_brief_json: dict | None = None   # Serialized DesignBrief
    room_dimensions_json: dict | None = None
    expected_category: str = ""
    expected_material: str = ""
    expected_dimensions: str = ""
    gold_url: str | None = None             # Optional known-good product URL


@dataclass
class AblationEntry:
    """One row in the ablation log: maps a query component to its success rate."""

    category: str
    component: str
    total_queries: int = 0
    link_alive_count: int = 0
    product_match_count: int = 0
    dimension_match_count: int = 0

    @property
    def link_alive_rate(self) -> float:
        return self.link_alive_count / self.total_queries if self.total_queries else 0.0

    @property
    def product_match_rate(self) -> float:
        return self.product_match_count / self.total_queries if self.total_queries else 0.0

    @property
    def dimension_match_rate(self) -> float:
        return self.dimension_match_count / self.total_queries if self.total_queries else 0.0


@dataclass
class BenchmarkReport:
    """Aggregate results from running the full benchmark suite."""

    num_cases: int
    total_queries: int
    avg_link_alive_rate: float
    avg_product_match_rate: float
    avg_dimension_match_rate: float
    per_category: dict[str, dict[str, float]] = field(default_factory=dict)
    trial_results: list[TrialResult] = field(default_factory=list)

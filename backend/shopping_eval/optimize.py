"""Shopping search optimization loop — autonomous prompt tuning via Claude Code.

Usage (from backend/):
    # Run baseline benchmark (tagged-queries strategy, default)
    python -m shopping_eval baseline

    # Run benchmark and compare against last baseline
    python -m shopping_eval compare

    # Use a different query strategy
    python -m shopping_eval baseline --strategy synthetic   # Score-Then-Search only
    python -m shopping_eval baseline --strategy both        # tagged + synthetic

    # Show ablation report (which query components work best per category)
    python -m shopping_eval ablation

Tuning loop — humans run the verbs, the CLI prints the signal:
1. `python -m shopping_eval baseline` → snapshot current behavior to results/baseline.json
2. Edit a prompt or `_build_search_queries_tagged` in app/activities/shopping.py
3. `python -m shopping_eval compare` → prints per-metric delta (IMPROVED / NEUTRAL / REGRESSED)
4. Keep the edit (commit) or drop it (`git restore`). The CLI does not mutate git.
5. `python -m shopping_eval ablation` → per-component success rates to guide the next edit.

Required env vars:
    EXA_API_KEY                   — always required
    ANTHROPIC_API_KEY             — required when --strategy includes synthetic
    EXA_CACHE_DIR (optional)      — file cache; makes re-runs $0 after first pass
    SYNTHETIC_LISTING_CACHE_DIR   — file cache for the Claude listing step
        (optional)

See docs/SHOPPING_EVAL.md for the full guide.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SHOPPING_EVAL_DIR = Path(__file__).parent
RESULTS_DIR = SHOPPING_EVAL_DIR / "results"
ABLATION_LOG = RESULTS_DIR / "ablation.jsonl"
BASELINE_FILE = RESULTS_DIR / "baseline.json"


def _ensure_dirs() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


async def cmd_baseline(exa_api_key: str, strategy: str, anthropic_api_key: str | None) -> None:
    """Run benchmark and save as baseline."""
    from .benchmark import load_benchmark_cases, run_benchmark

    _ensure_dirs()
    cases = load_benchmark_cases()
    print(f"Running baseline benchmark (strategy={strategy}) with {len(cases)} cases...")

    report = await run_benchmark(
        exa_api_key=exa_api_key,
        ablation_log_path=ABLATION_LOG,
        cases=cases,
        strategy=_coerce_strategy(strategy),
        anthropic_api_key=anthropic_api_key,
    )

    baseline = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "num_cases": report.num_cases,
        "total_queries": report.total_queries,
        "avg_link_alive_rate": report.avg_link_alive_rate,
        "avg_product_match_rate": report.avg_product_match_rate,
        "avg_dimension_match_rate": report.avg_dimension_match_rate,
        "per_category": report.per_category,
    }

    BASELINE_FILE.write_text(json.dumps(baseline, indent=2))
    _print_report(baseline)
    print(f"\nBaseline saved to {BASELINE_FILE}")


async def cmd_compare(exa_api_key: str, strategy: str, anthropic_api_key: str | None) -> None:
    """Run benchmark and compare against saved baseline."""
    from .benchmark import load_benchmark_cases, run_benchmark

    if not BASELINE_FILE.exists():
        print("No baseline found. Run `python -m shopping_eval baseline` first.")
        sys.exit(1)

    baseline = json.loads(BASELINE_FILE.read_text())
    cases = load_benchmark_cases()
    print(f"Running comparison benchmark (strategy={strategy}) with {len(cases)} cases...")

    report = await run_benchmark(
        exa_api_key=exa_api_key,
        ablation_log_path=ABLATION_LOG,
        cases=cases,
        strategy=_coerce_strategy(strategy),
        anthropic_api_key=anthropic_api_key,
    )

    current = {
        "avg_link_alive_rate": report.avg_link_alive_rate,
        "avg_product_match_rate": report.avg_product_match_rate,
        "avg_dimension_match_rate": report.avg_dimension_match_rate,
    }

    print("\n=== COMPARISON ===")
    for key in ["avg_link_alive_rate", "avg_product_match_rate", "avg_dimension_match_rate"]:
        old = baseline[key]
        new = current[key]
        delta = new - old
        arrow = "+" if delta >= 0 else ""
        label = key.replace("avg_", "").replace("_", " ").title()
        print(f"  {label}: {old:.1%} -> {new:.1%} ({arrow}{delta:.1%})")

    overall_delta = (
        current["avg_link_alive_rate"]
        + current["avg_product_match_rate"]
        - baseline["avg_link_alive_rate"]
        - baseline["avg_product_match_rate"]
    )
    if overall_delta > 0:
        print("\n  IMPROVED — consider accepting changes.")
    elif overall_delta < -0.05:
        print("\n  REGRESSED — consider reverting changes.")
    else:
        print("\n  NEUTRAL — no significant change.")


def cmd_ablation() -> None:
    """Print ablation report showing per-component effectiveness by category."""
    from .ablation import load_ablation_report

    if not ABLATION_LOG.exists():
        print("No ablation data. Run a benchmark first.")
        sys.exit(1)

    report = load_ablation_report(ABLATION_LOG)

    print("\n=== ABLATION REPORT ===")
    print("(Higher rates = component contributes more to finding good products)\n")

    for category in sorted(report.keys()):
        components = report[category]
        print(f"  {category.upper()}:")
        sorted_components = sorted(
            components.items(),
            key=lambda x: x[1]["product_rate"],
            reverse=True,
        )
        for comp_name, stats in sorted_components:
            total = stats["total"]
            link = stats["link_rate"]
            product = stats["product_rate"]
            print(f"    {comp_name:20s}  n={total:3d}  link={link:.0%}  product={product:.0%}")
        print()


def _print_report(data: dict) -> None:
    """Pretty-print benchmark results."""
    print("\n=== BENCHMARK RESULTS ===")
    print(f"  Cases: {data['num_cases']}")
    print(f"  Queries: {data['total_queries']}")
    print(f"  Link alive rate:     {data['avg_link_alive_rate']:.1%}")
    print(f"  Product match rate:  {data['avg_product_match_rate']:.1%}")
    print(f"  Dimension match rate: {data['avg_dimension_match_rate']:.1%}")

    if data.get("per_category"):
        print("\n  Per category:")
        for cat, rates in sorted(data["per_category"].items()):
            print(
                f"    {cat:20s}  link={rates['link_rate']:.0%}  "
                f"product={rates['product_rate']:.0%}  "
                f"n={rates.get('num_queries', '?')}"
            )


_VALID_STRATEGIES = ("tagged", "synthetic", "both")


def _coerce_strategy(raw: str):
    """Validate a strategy string; exits nonzero if invalid."""
    if raw not in _VALID_STRATEGIES:
        print(f"Unknown --strategy: {raw!r}. Expected one of {_VALID_STRATEGIES}.")
        sys.exit(1)
    return raw


def _parse_strategy(argv: list[str]) -> str:
    """Pull `--strategy <value>` out of argv; default to 'tagged'."""
    if "--strategy" not in argv:
        return "tagged"
    idx = argv.index("--strategy")
    if idx + 1 >= len(argv):
        print("--strategy requires a value (tagged|synthetic|both).")
        sys.exit(1)
    return argv[idx + 1]


def main() -> None:
    """CLI entrypoint."""
    import os

    if len(sys.argv) < 2:
        print(
            "Usage: python -m shopping_eval <baseline|compare|ablation> "
            "[--strategy tagged|synthetic|both]"
        )
        sys.exit(1)

    command = sys.argv[1]
    strategy = _parse_strategy(sys.argv)
    exa_api_key = os.environ.get("EXA_API_KEY", "")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY") or None

    needs_claude = strategy in ("synthetic", "both")

    if command == "ablation":
        cmd_ablation()
        return

    if command not in ("baseline", "compare"):
        print(f"Unknown command: {command}")
        sys.exit(1)

    if not exa_api_key:
        print("EXA_API_KEY environment variable required.")
        sys.exit(1)
    if needs_claude and not anthropic_api_key:
        print(f"ANTHROPIC_API_KEY required for --strategy {strategy}.")
        sys.exit(1)

    runner = cmd_baseline if command == "baseline" else cmd_compare
    asyncio.run(runner(exa_api_key, strategy, anthropic_api_key))


if __name__ == "__main__":
    main()

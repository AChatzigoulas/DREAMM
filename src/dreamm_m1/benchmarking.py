"""Repeatable model-independent benchmarks and separated comparison dimensions."""
from decimal import Decimal, InvalidOperation, localcontext
import statistics
import time
import os
import platform
import sys

from .identity import canonical, digest, require


def environment():
    return {"python": sys.version, "platform": platform.platform(),
        "CPU_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "native_thread_settings": {k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}}


def compare_artifact_bytes(before, after):
    require(isinstance(before, bytes) and isinstance(after, bytes), "BENCHMARK_INVALID", "raw bytes required")
    return {"byte_equal": before == after, "before_sha256": digest(before), "after_sha256": digest(after),
            "before_bytes": len(before), "after_bytes": len(after)}


def compare_records(before, after):
    """Explicit schemas/UIDs; exact numeric diagnostics, with no tolerance policy.

    Rows have {uid, values}; schema is the ordered column-name list. Numeric
    equality never substitutes for row identity, schema or raw-byte equality.
    """
    require(set(before) == set(after) == {"schema", "rows"}, "BENCHMARK_INVALID", "comparison envelope")
    def index(table):
        rows = table["rows"]
        require(len(rows) <= 20000 and len(table["schema"]) == len(set(table["schema"])), "BENCHMARK_INVALID", "comparison bounds/schema")
        out = {}
        for row in rows:
            require(set(row) == {"uid", "values"} and row["uid"] not in out and len(row["values"]) == len(table["schema"]),
                    "BENCHMARK_INVALID", "unique UID and row width")
            out[row["uid"]] = row["values"]
        return out
    a, b = index(before), index(after)
    same_schema = before["schema"] == after["schema"]
    exact_numeric, max_difference, compared = True, Decimal(0), 0
    nonnumeric = []
    if same_schema:
        for uid in sorted(a.keys() & b.keys()):
            for i, (x, y) in enumerate(zip(a[uid], b[uid])):
                try:
                    require(len(str(x)) <= 256 and len(str(y)) <= 256, "BENCHMARK_INVALID", "numeric token bound")
                    left, right = Decimal(str(x)), Decimal(str(y))
                    require(left.is_finite() and right.is_finite(), "BENCHMARK_INVALID", "nonfinite numeric comparison")
                    require(abs(left.adjusted()) <= 1000 and abs(right.adjusted()) <= 1000,
                            "BENCHMARK_INVALID", "numeric exponent bound")
                except InvalidOperation:
                    nonnumeric.append({"uid": uid, "column": before["schema"][i], "equal": x == y})
                    continue
                compared += 1
                # Exact bounded decimal subtraction; no default-context rounding
                # or tolerance is allowed to hide a changed retained token.
                with localcontext() as context:
                    context.prec = 2600
                    difference = abs(left - right)
                exact_numeric = exact_numeric and difference == 0
                max_difference = max(max_difference, difference)
    return {"schema": "dreamm-comparison/1", "canonical_bytes_equal": canonical(before) == canonical(after),
        "schema_equal": same_schema, "UID_set_equal": a.keys() == b.keys(),
        "row_order_equal": [r["uid"] for r in before["rows"]] == [r["uid"] for r in after["rows"]],
        "before_only_UIDs": sorted(a.keys() - b.keys()), "after_only_UIDs": sorted(b.keys() - a.keys()),
        "numeric_comparison_performed": same_schema, "numeric_cells_compared": compared,
        "exact_numeric_equal_on_common_UIDs": exact_numeric if same_schema and compared else None,
        "max_absolute_difference": str(max_difference) if same_schema else None,
        "nonnumeric_cells": nonnumeric, "tolerance": None, "scientific_parity_approved": False}


def benchmark_case(name, operation, *, iterations, evidence_class, fixture_sha256, expected_output_sha256=None):
    """Trusted local callable only, not an executable path or web request runner."""
    require(type(iterations) is int and 3 <= iterations <= 50 and evidence_class in ("synthetic", "public_static", "scientific_unvalidated", "scientific_validated"),
            "BENCHMARK_INVALID", "benchmark scope/repetitions")
    outputs, wall, cpu = [], [], []
    for _ in range(iterations):
        start, process = time.perf_counter_ns(), time.process_time_ns()
        value = operation()
        cpu.append(time.process_time_ns() - process)
        wall.append(time.perf_counter_ns() - start)
        raw = value if isinstance(value, bytes) else canonical(value)
        outputs.append(digest(raw))
    require(len(set(outputs)) == 1 and (expected_output_sha256 is None or outputs[0] == expected_output_sha256),
            "BENCHMARK_OUTPUT_CHANGED", "unstable or non-equivalent result; no timing acceptance")
    return {"schema": "dreamm-benchmark/1", "name": name, "evidence_class": evidence_class,
        "fixture_sha256": fixture_sha256, "iterations": iterations, "output_sha256": outputs[0],
        "environment": environment(),
        "wall_ns": wall, "self_cpu_ns": cpu, "median_wall_ns": statistics.median(wall),
        "median_self_cpu_ns": statistics.median(cpu), "warmup": "none_each_call_reported",
        "complete_scientific_workload": False, "scientific_parity_approved": False}


def compare_benchmarks(before, after):
    require(all(before[k] == after[k] for k in ("schema", "name", "evidence_class", "fixture_sha256", "output_sha256", "iterations", "environment")),
            "BENCHMARK_INVALID", "comparison needs matching scope/input/output/repetition contracts")
    return {"schema": "dreamm-benchmark-comparison/1", "before_median_wall_ns": before["median_wall_ns"],
        "after_median_wall_ns": after["median_wall_ns"],
        "median_ratio_before_over_after": before["median_wall_ns"] / after["median_wall_ns"],
        "scope": before["evidence_class"], "complete_DREAMM_speedup_claim": False}

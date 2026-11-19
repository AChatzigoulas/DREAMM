"""Offline HHblits/ProtDCal benchmark contracts, never a scientific executor.

Uses the existing M1 comparison and identity primitives. Counter/timeline input
must eventually come from a verified external observer; JSON claims do not
attest containment, cold caches, scientific equality or authorization.
"""
from copy import deepcopy
from .identity import canonical, digest, require, sha
from .benchmarking import compare_artifact_bytes, compare_records

HH_PARAMETERS = ("-M", "first", "-n", "3", "-v", "0")
HH_WRAPPER_SHA256 = "0df4b1d173225eee34d8fdd91dbd535ab9ba3699f94fc969b9a1b3b2d73e338b"
HH_LEGACY_BINARY_SHA256 = "76ef2b7a7bdb161c9604181aabfa2820ec06f8c27cdcdf66404f9f229f9df7e9"
THREAD_COUNTS = (1, 2, 4, 8, 15, 16)
HASH_FIELDS = ("binary_sha256", "build_sha256", "runtime_sha256", "defaults_sha256", "database_sha256")
COUNTERS = ("cpu_usage_usec", "read_bytes", "write_bytes", "pgfault", "pgmajfault", "throttled_usec", "oom_kill")
GAUGES = ("memory_current_bytes", "memory_peak_bytes", "tasks_current", "tasks_peak")
PHASES = frozenset(("startup", "database_open", "prefilter", "search", "jvm_startup", "descriptor_total",
                    "DGel", "DGLJ", "descriptor_parse", "artifact_copy"))


def query_input(sequence, residue_uids):
    """Accept an already evidenced DSSP-order query; never derive/normalize one.

    Cross-chain concatenation/order is legacy behavior; no chain splitting or
    coordinate-based alignment. Identity bindings are local to each request.
    """
    require(isinstance(sequence, str) and 0 < len(sequence) <= 20000
            and all("A" <= x <= "Z" for x in sequence),
            "BENCHMARK_INPUT_INVALID", "explicit uppercase query required; no normalization/filtering")
    require(isinstance(residue_uids, (tuple, list)) and len(residue_uids) == len(sequence)
            and all(isinstance(x, str) and 0 < len(x) <= 128 for x in residue_uids)
            and len(set(residue_uids)) == len(residue_uids),
            "IDENTITY_AMBIGUOUS", "one explicit unique residue identity per query position required")
    raw = (">\n" + sequence + "\n").encode("ascii")
    return raw, {"schema": "dreamm-HH-query/1", "fasta_sha256": digest(raw), "sequence": sequence,
                 "positions": [{"position": i + 1, "residue_uid": uid, "symbol": sequence[i]} for i, uid in enumerate(residue_uids)],
                 "sequence_extraction_performed": False, "source": "caller_must_supply_verified_legacy_DSSP_order_receipt"}


def database_fingerprint(files):
    """Packed bytes/indexes/aliases, independent of installation directory.

    Fingerprints are comparison metadata, not publisher authentication or proof
    that the actual database was reconstructed and checked on a clean server.
    """
    require(isinstance(files, list) and len(files) == 13, "BENCHMARK_INPUT_INVALID", "11 files and two aliases required")
    seen = set()
    for f in files:
        require(set(f) == {"name", "kind", "bytes", "sha256", "target"}
                and isinstance(f["name"], str) and "/" not in f["name"] and "\\" not in f["name"]
                and f["name"].startswith("uniclust30_2018_08") and f["name"] not in seen
                and type(f["bytes"]) is int and f["bytes"] > 0, "BENCHMARK_INPUT_INVALID", "database entry")
        seen.add(f["name"])
        if f["kind"] == "regular":
            sha(f["sha256"])
            require(f["target"] is None, "BENCHMARK_INPUT_INVALID", "regular database entry")
        else:
            require(f["kind"] == "link" and f["sha256"] is None
                    and f["name"] in ("uniclust30_2018_08_a3m_db", "uniclust30_2018_08_hhm_db")
                    and f["target"] == f["name"].removesuffix("_db") + ".ffdata",
                    "BENCHMARK_INPUT_INVALID", "only preserved relative aliases allowed")
    core = {"uniclust30_2018_08_" + family + suffix for family in ("a3m", "hhm", "cs219") for suffix in (".ffdata", ".ffindex")}
    extra = {"uniclust30_2018_08" + s for s in (".cs219", ".cs219.sizes", "_a3m_db.index", "_hhm_db.index", "_md5sum", "_a3m_db", "_hhm_db")}
    require(seen == core | extra and sum(f["kind"] == "regular" for f in files) == 11,
            "BENCHMARK_INPUT_INVALID", "database family/type set incomplete")
    return digest(canonical(sorted(files, key=lambda f: f["name"])))


def search_context(*, threads, **fingerprints):
    require(type(threads) is int and threads in THREAD_COUNTS and set(fingerprints) == set(HASH_FIELDS),
            "BENCHMARK_INPUT_INVALID", "thread experiment and pinned tool/default/database identities required")
    for value in fingerprints.values(): sha(value)
    return {"schema": "dreamm-HH-context/1", "release": "3.3.0", "threads": threads,
            "parameters": list(HH_PARAMETERS), "wrapper_contract_sha256": HH_WRAPPER_SHA256, **fingerprints}


def validate_context(context):
    require(set(context) == {"schema", "release", "threads", "parameters", "wrapper_contract_sha256", *HASH_FIELDS},
            "BENCHMARK_INPUT_INVALID", "context fields")
    rebuilt = search_context(threads=context["threads"], **{k: context[k] for k in HASH_FIELDS})
    require(context == rebuilt, "BENCHMARK_INPUT_INVALID", "search parameters/version/adapter must remain unchanged")


def hhblits_request(sequence, residue_uids, context):
    validate_context(context)
    raw, query = query_input(sequence, residue_uids)
    return raw, {"schema": "dreamm-HH-request/1", "query": query, "context": deepcopy(context),
        "argv": ["/opt/dreamm-hhsuite/bin/hhblits", "-i", "/work/hhblits/input.fasta", "-d",
                 "/data/uniclust30_2018_08/uniclust30_2018_08", "-cpu", str(context["threads"]),
                 "-M", "first", "-n", "3", "-ohhm", "/work/hhblits/output.hhm", "-v", "0"],
        "shell": False, "execution_authorized": False, "implicit_defaults": "bound_to_binary_build_runtime_and_defaults_receipt",
        "postprocessing": "legacy_NULL_row_removal_and_DSSP_identity_rebinding_pending_runtime_validation"}


def query_cache_key(request):
    validate_context(request["context"])
    query = request["query"]
    raw, expected = hhblits_request(query["sequence"], [p["residue_uid"] for p in query["positions"]], request["context"])
    require(query == expected["query"], "IDENTITY_AMBIGUOUS", "query identity receipt changed")
    require(request == expected, "BENCHMARK_INPUT_INVALID", "fixed argv/authority envelope changed; cannot key a different search")
    # IDs/coordinates are deliberately not cached: only raw sequence-search
    # output could be reused; every consumer must independently rebind residues.
    return digest(canonical({"schema": "dreamm-HH-raw-cache-key/1", "fasta_sha256": digest(raw),
                             "context": request["context"], "output": "raw_HHM_unmodified/1"}))


def cache_candidate(request, raw_hhm):
    require(isinstance(raw_hhm, bytes) and 0 < len(raw_hhm) <= 8 * 1024**2,
            "BENCHMARK_INPUT_INVALID", "bounded opaque HHM candidate")
    return {"schema": "dreamm-HH-cache-candidate/1", "key": query_cache_key(request),
            "hhm_sha256": digest(raw_hhm), "bytes": len(raw_hhm), "cache_use_authorized": False,
            "HHM_semantically_validated": False, "residue_bindings_cached": False,
            "gate": "trusted_execution_cleanup_HHM_validation_real_fixture_comparison_and_owner_approval"}


class PhaseTimeline:
    """Explicit observer events only; no estimates from quiet logs or wall time."""
    def __init__(self): self._events, self._open, self._complete = [], {}, []

    def event(self, phase, edge, ns, *, iteration=0):
        require(phase in PHASES and edge in ("begin", "end") and type(ns) is int and ns >= 0
                and type(iteration) is int and 0 <= iteration <= 3 and len(self._events) < 256,
                "BENCHMARK_INPUT_INVALID", "bounded observer event")
        require(not self._events or ns >= self._events[-1]["ns"], "BENCHMARK_INPUT_INVALID", "one monotonic observer clock required")
        key = phase, iteration
        if edge == "begin":
            require(key not in self._open and not any(x["phase"] == phase and x["iteration"] == iteration for x in self._complete),
                    "BENCHMARK_INPUT_INVALID", "duplicate phase")
            self._open[key] = ns
        else:
            require(key in self._open, "BENCHMARK_INPUT_INVALID", "phase end without start")
            self._complete.append({"phase": phase, "iteration": iteration, "duration_ns": ns - self._open.pop(key)})
        self._events.append({"phase": phase, "edge": edge, "ns": ns, "iteration": iteration})

    def receipt(self):
        return {"schema": "dreamm-hotspot-timeline/1", "events": deepcopy(self._events), "completed": deepcopy(self._complete),
                "incomplete": [{"phase": k[0], "iteration": k[1]} for k in self._open],
                "unmeasured_phases": sorted(PHASES - {x["phase"] for x in self._complete}),
                "measurement_attestation": "external_observer_verification_required_not_attested_by_JSON"}


def resource_summary(before, after):
    """Whole-cgroup counter deltas, never per-process RSS mislabeled as job peak."""
    fields = {"schema", "scope", "unit_instance_sha256", "monotonic_ns", *COUNTERS, *GAUGES}
    for record in (before, after):
        require(set(record) == fields and record["schema"] == "dreamm-cgroup-sample/1"
                and record["scope"] == "whole_job_cgroup", "BENCHMARK_INPUT_INVALID", "whole-job counter schema/scope")
        sha(record["unit_instance_sha256"])
        require(type(record["monotonic_ns"]) is int and record["monotonic_ns"] >= 0,
                "BENCHMARK_INPUT_INVALID", "sample clock")
        require(all(record[k] is None or type(record[k]) is int and record[k] >= 0 for k in COUNTERS + GAUGES),
                "BENCHMARK_INPUT_INVALID", "nonnegative measured counters or explicit null")
    require(before["unit_instance_sha256"] == after["unit_instance_sha256"], "BENCHMARK_INPUT_INVALID", "foreign/reused unit")
    wall = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
    require(wall > 0, "BENCHMARK_INPUT_INVALID", "positive observation interval")
    for k in ("memory_peak_bytes", "tasks_peak"):
        require(before[k] is None or after[k] is None or after[k] >= before[k],
                "BENCHMARK_INPUT_INVALID", "peak reset requires a new cgroup identity")
    deltas = {}
    for k in COUNTERS:
        deltas[k] = None if before[k] is None or after[k] is None else after[k] - before[k]
        require(deltas[k] is None or deltas[k] >= 0, "BENCHMARK_INPUT_INVALID", "counter reset/identity mismatch")
    return {"schema": "dreamm-hotspot-resources/1", "scope": "whole_job_cgroup", "wall_seconds": wall, **deltas,
            "CPU_percent_one_core_100": None if deltas["cpu_usage_usec"] is None else deltas["cpu_usage_usec"] / 1e6 / wall * 100,
            "read_bytes_per_second": None if deltas["read_bytes"] is None else deltas["read_bytes"] / wall,
            "write_bytes_per_second": None if deltas["write_bytes"] is None else deltas["write_bytes"] / wall,
            "memory_peak_bytes": after["memory_peak_bytes"], "tasks_peak": after["tasks_peak"],
            "memory_scope": "cgroup_memory_including_page_cache_not_summed_RSS",
            "peak_scope": "unit_lifetime_not_stage_peak_requires_fresh_attempt_cgroup",
            "measurement_attestation": "external_verified_collector_required", "resource_enforcement_proved_by_this_function": False}


def benchmark_matrix(fixture, context):
    """A bounded sequential experiment specification, never a scheduler/admission."""
    validate_context(context)
    require(set(fixture) == {"id", "source_sha256", "query_sha256", "identity_sha256", "residue_count", "size_class"}
            and fixture["id"] in ("1RLW", "3AKM", "2KS4", "2RSG")
            and type(fixture["residue_count"]) is int and 0 < fixture["residue_count"] <= 20000
            and fixture["size_class"] in ("small", "medium", "large"), "BENCHMARK_INPUT_INVALID", "approved public fixture evidence required")
    for k in ("source_sha256", "query_sha256", "identity_sha256"): sha(fixture[k])
    cases = [{"threads": n, "cache_condition_requested": condition, "repetition": r}
             for n in THREAD_COUNTS for condition in ("uncontrolled", "warm_conditioned", "cold_requested") for r in (1, 2, 3)]
    return {"schema": "dreamm-HH-experiment/1", "fixture": deepcopy(fixture), "baseline_context": deepcopy(context), "cases": cases,
            "execution_authorized": False, "run_order": "explicit_future_counterbalanced_sequential_one_case_at_a_time",
            "maximum_complete_jobs": 2, "benchmark_complete_jobs_at_once": 1, "target_vCPU": 16, "target_RAM_GiB": 16,
            "cache_rules": "warm_conditioned_is_not_all_86GiB_resident_cold_requires_external_proof_no_global_cache_drop",
            "resource_gate": "verified_aggregate_limits_and_measured_size_admission_no_automatic_large_fixture_execution",
            "science_parameters_changed": False}


def compare_search_runs(before, after, *, variable):
    """Only one declared dimension varies; preserve changed outputs as failures."""
    require(variable in ("threads", "cache_condition", "build"), "BENCHMARK_INPUT_INVALID", "single comparison dimension")
    for run in (before, after):
        validate_context(run["context"])
        sha(run["query_sha256"]); sha(run["identity_sha256"]); sha(run["output_sha256"]); sha(run["host_storage_sha256"])
        require(run["cache_condition"] in ("uncontrolled", "warm_conditioned", "cold_verified")
                and (run["cache_condition"] != "cold_verified" or isinstance(run.get("cache_evidence_sha256"), str)),
                "BENCHMARK_INPUT_INVALID", "cold condition requires external evidence")
        require(run["cache_scope"] in ("unknown", "observed_working_set", "guest_page_cache_only")
                and (run["cache_condition"] != "cold_verified" or run["cache_scope"] == "guest_page_cache_only"),
                "BENCHMARK_INPUT_INVALID", "cold guest cache is not proof of cold hypervisor/storage caches")
        if run.get("cache_evidence_sha256") is not None: sha(run["cache_evidence_sha256"])
        require(type(run["wall_ns"]) is int and run["wall_ns"] > 0 and run["evidence_class"] in ("synthetic", "scientific_unvalidated", "scientific_validated"),
                "BENCHMARK_INPUT_INVALID", "comparison scope/wall time")
    a, b = deepcopy(before["context"]), deepcopy(after["context"])
    if variable == "threads": a.pop("threads"); b.pop("threads")
    if variable == "build":
        for key in ("binary_sha256", "build_sha256", "runtime_sha256"): a.pop(key); b.pop(key)
    require(a == b and all(before[k] == after[k] for k in ("query_sha256", "identity_sha256", "host_storage_sha256", "evidence_class"))
            and (variable == "cache_condition" or (before["cache_condition"], before["cache_scope"]) == (after["cache_condition"], after["cache_scope"])),
            "BENCHMARK_INPUT_INVALID", "confounded benchmark comparison")
    equal = before["output_sha256"] == after["output_sha256"]
    return {"schema": "dreamm-HH-comparison/1", "variable": variable, "output_bytes_equal_by_SHA256": equal,
            "observed_wall_ratio_before_over_after": before["wall_ns"] / after["wall_ns"],
            "speedup_claim_allowed": False, "scientific_parity_approved": False,
            "status": "candidate_equal_bytes" if equal else "OUTPUT_CHANGED_requires_separate_identity_schema_numeric_review",
            "scope": before["evidence_class"], "tolerance": None}


def compare_descriptor_outputs(before, after, *, before_rows, after_rows):
    """Existing M1 raw-byte and UID/schema/numeric contracts remain independent."""
    return {"raw_AA": compare_artifact_bytes(before, after), "mapped_table": compare_records(before_rows, after_rows),
            "whole_protein_cross_chain_semantics_required": True, "optimized_kernel_authorized": False,
            "classifier_columns": 727, "already_omitted_ProtDCal_zero_columns": 167}

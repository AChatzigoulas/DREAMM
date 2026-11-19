"""Optional stdlib measurements; sidecars only, never scientific parameters.

Self-process counters are not whole-job/cgroup enforcement. Missing measurements
are null with a reason; process-lifetime RSS high-water is not stage peak RSS.
Nested spans are inclusive and must not be summed as independent stages.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import platform
import time
try:
    import resource
except ImportError:
    resource = None

from .identity import canonical, digest, require

_ACTIVE = ContextVar("dreamm_stage_recorder", default=None)
EVIDENCE = frozenset(("synthetic", "public_static", "scientific_unvalidated", "scientific_validated"))


def snapshot():
    result = {"clock_ns": time.perf_counter_ns(), "self_cpu_ns": time.process_time_ns(),
        "rss_bytes": None, "process_lifetime_peak_rss_bytes": None, "thread_count": None,
        "disk_read_bytes": None, "disk_write_bytes": None, "read_chars": None, "write_chars": None,
        "reaped_children_cpu_seconds": None}
    if resource is not None:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            result["process_lifetime_peak_rss_bytes"] = int(usage.ru_maxrss * (1 if platform.system() == "Darwin" else 1024))
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            result["reaped_children_cpu_seconds"] = children.ru_utime + children.ru_stime
        except (OSError, ValueError):
            pass
    if platform.system() == "Linux":
        try:
            data = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
            result["rss_bytes"] = int(data["VmRSS"].split()[0]) * 1024
            result["thread_count"] = int(data["Threads"].strip())
            io = dict(line.split(":", 1) for line in Path("/proc/self/io").read_text().splitlines())
            for field, key in (("disk_read_bytes", "read_bytes"), ("disk_write_bytes", "write_bytes"),
                               ("read_chars", "rchar"), ("write_chars", "wchar")):
                result[field] = int(io[key].strip())
        except (OSError, ValueError, KeyError):
            pass
    return result


class StageRecorder:
    def __init__(self, *, evidence_class, max_spans=1000):
        require(evidence_class in EVIDENCE and type(max_spans) is int and 0 < max_spans <= 10000,
                "METRIC_INVALID", "measurement scope/bound")
        self.evidence_class, self.max_spans = evidence_class, max_spans
        self.spans, self.artifacts, self.events = [], [], []
        self._stack = []

    @contextmanager
    def activate(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)

    @contextmanager
    def stage(self, name):
        require(isinstance(name, str) and 0 < len(name) <= 96 and len(self.spans) < self.max_spans,
                "METRIC_INVALID", "stage name/span bound")
        start = snapshot()
        span = {"index": len(self.spans), "stage": name, "parent_index": self._stack[-1] if self._stack else None,
                "inclusive": True, "status": "running", "error_code": None}
        self.spans.append(span)
        self._stack.append(span["index"])
        try:
            yield span
            span["status"] = "succeeded"
        except BaseException as exc:
            span.update(status="failed", error_code=getattr(exc, "code", type(exc).__name__))
            raise
        finally:
            end = snapshot()
            self._stack.pop()
            def delta(key):
                return None if start[key] is None or end[key] is None else end[key] - start[key]
            span.update(wall_ns=delta("clock_ns"), self_cpu_ns=delta("self_cpu_ns"),
                self_process_count=1,
                reaped_children_cpu_seconds=delta("reaped_children_cpu_seconds"),
                rss_start_bytes=start["rss_bytes"], rss_end_bytes=end["rss_bytes"],
                process_lifetime_peak_rss_bytes=end["process_lifetime_peak_rss_bytes"],
                thread_count_start=start["thread_count"], thread_count_end=end["thread_count"],
                disk_read_bytes=delta("disk_read_bytes"), disk_write_bytes=delta("disk_write_bytes"),
                read_chars=delta("read_chars"), write_chars=delta("write_chars"),
                stage_peak_memory_bytes=None, whole_tree_process_count=None,
                whole_tree_thread_peak=None, tool_startup_ns=None,
                unavailable_reason="no_verified_whole_job_sampler_or_tool_ready_event")

    def record_artifacts(self, payloads):
        require(len(self.artifacts) < self.max_spans and hasattr(payloads, "items"), "METRIC_INVALID", "artifact ledger bound")
        sizes = {name: len(raw) for name, raw in payloads.items() if isinstance(raw, bytes)}
        self.artifacts.append({"span_index": self._stack[-1] if self._stack else None,
                               "sizes_bytes": sizes, "total_bytes": sum(sizes.values())})

    def tool_startup(self, *, stage, spawned_ns, ready_ns, clock_domain):
        require(clock_domain == "same_process_perf_counter" and type(spawned_ns) is int and type(ready_ns) is int
                and 0 <= spawned_ns <= ready_ns and len(self.events) < self.max_spans,
                "METRIC_INVALID", "startup requires explicit readiness in the same clock domain")
        self.events.append({"stage": stage, "tool_startup_ns": ready_ns - spawned_ns,
                            "evidence": "explicit_ready_event", "clock_domain": clock_domain})

    def receipt(self):
        require(not self._stack, "METRIC_INVALID", "cannot seal active spans")
        return {"schema": "dreamm-stage-measurements/1", "evidence_class": self.evidence_class,
            "pid": os.getpid(), "measurement_scope": "self_process_and_reaped_children_counters_only",
            "rss_method": "Linux_proc_endpoints_and_process_lifetime_getrusage_high_water_not_stage_peak",
            "io_method": "self_proc_io_including_observer_io_not_whole_tree",
            "nested_spans": "inclusive_do_not_sum", "resource_enforcement": False,
            "CPU_attribution": "process_all_threads_not_exclusive_stage_with_concurrency",
            "spans": self.spans, "artifacts": self.artifacts, "tool_events": self.events}


def measured(name):
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            recorder = _ACTIVE.get()
            if recorder is None:
                return function(*args, **kwargs)
            with recorder.stage(name):
                return function(*args, **kwargs)
        return wrapper
    return decorate


def note_artifacts(payloads):
    recorder = _ACTIVE.get()
    if recorder is not None:
        recorder.record_artifacts(payloads)

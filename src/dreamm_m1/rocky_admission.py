"""Admission and effective limit checks for the future Rocky coordinator.

These functions do not reserve a queue slot, create a unit or change a limit.
Call under the existing claim lock. Missing broker/cleanup evidence is a launch
failure regardless of what this policy returns.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import time

from .identity import require


GIB = 1024**3
MIB = 1024**2
DATABASE_BYTES = 92_752_937_470
M1_SECONDS = {"hhblits": 14400, "protdcal": 7200}
M5_SECONDS = {"generation": 7200, "preparation": 4500, "p2rank": 1200, "essa": 2400, "analysis": 21900}


@dataclass(frozen=True)
class Capacity:
    memory_total: int
    memory_available: int
    cpus: int
    disk_available: int


def read_capacity(data_root):
    """Fresh host sample. f_bavail excludes blocks unavailable to non-root."""
    fields = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, value = line.split(":", 1)
        if key in ("MemAvailable", "MemTotal"):
            amount, unit = value.split()
            require(unit == "kB" and amount.isdecimal(), "ADMISSION_UNAVAILABLE", "invalid host RAM counter")
            fields[key] = int(amount) * 1024
    require(set(fields) == {"MemAvailable", "MemTotal"}, "ADMISSION_UNAVAILABLE", "missing live RAM counters")
    disk = os.statvfs(data_root)
    return Capacity(fields["MemTotal"], fields["MemAvailable"], len(os.sched_getaffinity(0)),
                    disk.f_bavail * disk.f_frsize)


@dataclass(frozen=True)
class Limits:
    memory_bytes: int
    scratch_bytes: int
    runtime_seconds: int
    cpu_cores: int = 15
    tasks: int = 128


def admit_heavy(capacity, *, active_jobs, mode, host_reserve=2 * GIB, disk_reserve=12 * GIB):
    """Initial serial profiles, conservatively calibrated against OLD-VM evidence."""
    require(mode in ("M1", "M5") and type(active_jobs) is int and active_jobs >= 0,
            "ADMISSION_UNAVAILABLE", "unknown job class or live count")
    require(type(host_reserve) is int and host_reserve >= 2 * GIB
            and type(disk_reserve) is int and disk_reserve >= 12 * GIB,
            "ADMISSION_UNAVAILABLE", "host or data-volume reserve too small")
    require(all(type(value) is int and value >= 0 for value in vars(capacity).values())
            and capacity.memory_available <= capacity.memory_total,
            "ADMISSION_UNAVAILABLE", "invalid live capacity sample")
    limits = Limits(12 * GIB, 2 * GIB, 18000) if mode == "M1" else Limits(12 * GIB + 128 * MIB, 8 * GIB, 28800)
    require(active_jobs == 0, "ADMISSION_BUSY", "initial policy allows only one heavy attempt")
    require(capacity.cpus >= 16 and capacity.memory_available >= limits.memory_bytes + host_reserve
            and capacity.disk_available >= limits.scratch_bytes + disk_reserve,
            "ADMISSION_BUSY", "live CPU, RAM or scratch headroom is insufficient")
    return limits


def verify_effective_limits(cgroup, limits, *, io_device, read_bps, write_bps):
    """Verify the known job cgroup. Its ancestry, identity and lifecycle are broker gates."""
    group = Path(cgroup)

    def read(name):
        return (group / name).read_text(encoding="ascii").strip()

    cpu = read("cpu.max").split()
    require(len(cpu) == 2 and all(x.isdecimal() and int(x) > 0 for x in cpu)
            and int(cpu[0]) == limits.cpu_cores * int(cpu[1])
            and read("memory.max") == str(limits.memory_bytes) and read("memory.swap.max") == "0"
            and read("pids.max") == str(limits.tasks),
            "ADMISSION_UNAVAILABLE", "cgroup CPU, memory, swap or task limits differ")
    require(all(type(n) is int and n > 0 for n in (read_bps, write_bps)),
            "ADMISSION_UNAVAILABLE", "explicit I/O ceilings required")
    for line in read("io.max").splitlines():
        device, *values = line.split()
        if device == io_device:
            effective = dict(item.split("=", 1) for item in values)
            require(effective.get("rbps") == str(read_bps) and effective.get("wbps") == str(write_bps),
                    "ADMISSION_UNAVAILABLE", "effective I/O ceilings differ")
            return
    require(False, "ADMISSION_UNAVAILABLE", "no effective I/O limits for the data device")


@dataclass(frozen=True)
class Deadline:
    """Persist the original boot ID and CLOCK_BOOTTIME expiry in the broker ledger."""
    boot_id: str
    expires: float

    def remaining(self, maximum, *, boot_id, now):
        require(boot_id == self.boot_id and 0 <= now < self.expires and maximum > 0,
                "TOOL_TIMEOUT", "attempt deadline expired or boot identity changed")
        return min(maximum, self.expires - now)

    def stage(self, maximum):
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return self.remaining(maximum, boot_id=boot_id, now=time.clock_gettime(time.CLOCK_BOOTTIME))


def can_start_essa(*, capacity, memory_current, memory_max, scan_bytes, active_scans, host_reserve=2 * GIB):
    """Conservative additive reserve for one or two independent one-thread scans."""
    require(all(type(n) is int and n >= 0 for n in (memory_current, memory_max, scan_bytes, active_scans, host_reserve))
            and scan_bytes > 0 and host_reserve >= 2 * GIB and active_scans <= 2,
            "ADMISSION_UNAVAILABLE", "invalid ESSA memory reservation")
    required = (active_scans + 1) * scan_bytes
    return (active_scans < 2 and memory_current + required <= memory_max
            and required + host_reserve <= capacity.memory_available)


def essa_gate(data_root, cgroup, *, scan_bytes, host_reserve=2 * GIB):
    """Return the live gate consumed by the M5 dispatcher at each scan start."""
    group = Path(cgroup)

    def allow(active_scans):
        return can_start_essa(capacity=read_capacity(data_root), active_scans=active_scans,
                              memory_current=int((group / "memory.current").read_text(encoding="ascii")),
                              memory_max=int((group / "memory.max").read_text(encoding="ascii")),
                              scan_bytes=scan_bytes, host_reserve=host_reserve)

    return allow

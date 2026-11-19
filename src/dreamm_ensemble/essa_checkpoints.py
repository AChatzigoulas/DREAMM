"""Schedule at most two isolated ESSA scans inside one admitted M5 attempt."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import math

from dreamm_m1.capture import _json
from dreamm_m1.identity import canonical, digest, require, sha
from dreamm_m1.rocky_admission import M5_SECONDS


PROFILE = {"id": "essa-gnm-prody2.2.0-openblas1-v1", "openblas_threads": 1,
           "enm": "gnm", "n_modes": 10, "cutoff": None, "lowmem": True}
THREAD_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
RESULT = "essa.json"


@dataclass(frozen=True)
class Frame:
    index: int
    sha256: str
    residue_map_sha256: str
    ca_ids: tuple


def _validate(raw, frame):
    row = _json(raw)
    require(isinstance(row, dict) and type(row.get("frame")) is int and row["frame"] == frame.index
            and row.get("sha256") == frame.sha256 and row.get("profile") == PROFILE
            and type(row.get("threads")) is int and row["threads"] == 1 and row.get("openblas")
            and isinstance(row.get("scores"), dict) and set(row["scores"]) == set(frame.ca_ids)
            and all(type(value) in (int, float) and math.isfinite(value) for value in row["scores"].values()),
            "ESSA_CHECKPOINT_INVALID", "incomplete or unbound one-thread scan")
    return row


def run_essa_frames(frames, *, store, runtime_sha256, deadline, can_start, scan):
    """Use the admitted worker's fixed subprocess runner and fresh memory gate.

    scan(frame, timeout, env) must verify frame bytes before invoking the exact
    versioned scan, return its complete receipt, and enforce descendant cleanup.
    can_start(active_scans) must use the live host and worker reservations.
    """
    frames = tuple(frames)
    require(1 <= len(frames) <= 51 and tuple(f.index for f in frames) == tuple(range(len(frames))),
            "ESSA_CHECKPOINT_INVALID", "incomplete or unordered frame set")
    sha(runtime_sha256)
    outputs, pending = {}, []
    profile_sha256 = digest(canonical(PROFILE))
    for frame in frames:
        sha(frame.sha256)
        sha(frame.residue_map_sha256)
        require(type(frame.index) is int and 0 < len(frame.ca_ids) <= 20000
                and len(set(frame.ca_ids)) == len(frame.ca_ids)
                and all(type(key) is str and 0 < len(key) <= 128 for key in frame.ca_ids),
                "ESSA_CHECKPOINT_INVALID", "invalid C-alpha correspondence")
        stage = "essa-{:02d}".format(frame.index)
        inputs = {"frame": frame.sha256, "mapping": frame.residue_map_sha256,
                  "ca-ids": digest(canonical(frame.ca_ids)), "profile": profile_sha256, "runtime": runtime_sha256}
        saved = store.load(stage, inputs, (RESULT,))
        if saved is None:
            pending.append((frame, stage, inputs))
        else:
            outputs[frame.index] = _validate(saved[RESULT], frame)

    def run(frame, stage, inputs):
        raw = scan(frame, deadline.stage(M5_SECONDS["essa"]), dict(THREAD_ENV))
        row = _validate(raw, frame)
        store.commit(stage, inputs, {RESULT: raw})
        return row

    with ThreadPoolExecutor(max_workers=2) as executor:
        running = {}
        while pending or running:
            while pending and len(running) < 2 and can_start(len(running)) is True:
                frame, stage, inputs = pending.pop(0)
                running[executor.submit(run, frame, stage, inputs)] = frame.index
            require(running, "ADMISSION_BUSY", "insufficient headroom for the next ESSA frame")
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                outputs[running.pop(future)] = future.result()
    return tuple(outputs[frame.index] for frame in frames)

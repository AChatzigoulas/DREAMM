"""Verify actual preserved reference/list bytes, without fitting or model I/O."""
from collections import Counter
import csv
import io
from collections import OrderedDict
from copy import deepcopy
from threading import Lock

from .features import Profile, REFERENCE_SHA256
from .identity import digest, require
from .measurement import measured

ZERO_FILE_SHA256 = "b7f0ae8175ff2abdbe5a248f065b06732e28676e093e69d174fe5188ea95d867"


class ReferenceValidationCache:
    """Explicit worker-owned cache of binding receipts, not data/scaler/model.

    Every lookup hashes actual immutable bytes; max four small receipts, no
    global state or path/mtime keys. Failed validation is never cached.
    """
    def __init__(self, max_entries=2):
        require(type(max_entries) is int and 1 <= max_entries <= 4, "RESOURCE_LIMIT", "reference cache bound")
        self.max_entries, self._entries, self._lock = max_entries, OrderedDict(), Lock()
        self.hits, self.misses = 0, 0

    @measured("reference.worker_binding_reuse")
    def verify(self, reference, zeroed, profile_bytes):
        require(all(isinstance(x, bytes) for x in (reference, zeroed, profile_bytes))
                and 0 < len(reference) <= 16 * 1024**2 and len(zeroed) <= 32768 and len(profile_bytes) <= 1024**2,
                "FEATURE_SCHEMA_MISMATCH", "reference cache immutable byte bounds")
        key = ("reference-binding/1", digest(reference), digest(zeroed), digest(profile_bytes))
        with self._lock:
            if key in self._entries:
                self.hits += 1
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key])
            receipt = verify_reference_assets(reference, zeroed, profile_bytes)
            self.misses += 1
            self._entries[key] = deepcopy(receipt)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return receipt


@measured("reference.validate_CSV_binding")
def verify_reference_assets(reference, zeroed, profile_bytes):
    profile = Profile.owner_selected(profile_bytes)
    require(isinstance(reference, bytes) and 0 < len(reference) <= 16 * 1024**2
            and digest(reference) == REFERENCE_SHA256,
            "FEATURE_SCHEMA_MISMATCH", "preserved reference file hash")
    require(isinstance(zeroed, bytes) and len(zeroed) <= 32768 and digest(zeroed) == ZERO_FILE_SHA256,
            "FEATURE_SCHEMA_MISMATCH", "preserved 167-column file hash")
    zero_reader = csv.reader(io.StringIO(zeroed.decode("utf-8-sig"), newline=""))
    zero_header = next(zero_reader)
    require(zero_header == ["", "0"], "FEATURE_SCHEMA_MISMATCH", "zero-list CSV schema")
    rows = list(zero_reader)
    require(len(rows) == 167 and all(len(r) == 2 for r in rows)
            and len({r[1] for r in rows}) == 167
            and {r[1] for r in rows} == profile.zeroed,
            "FEATURE_SCHEMA_MISMATCH", "actual zero-list names differ from core")
    reader = csv.reader(io.StringIO(reference.decode("utf-8-sig"), newline=""))
    header = next(reader)
    require(len(header) == 729 and header[0] == "" and len(set(header)) == 729 and "class" in header,
            "FEATURE_SCHEMA_MISMATCH", "reference CSV schema")
    names = [name for name in header[1:] if name != "class"]
    require(names == list(profile.names), "FEATURE_SCHEMA_MISMATCH", "physical reference feature order differs from core")
    classes, count = Counter(), 0
    class_at = header.index("class")
    for row in reader:
        count += 1
        require(count <= 3010 and len(row) == len(header) and row[class_at] in ("0", "1"),
                "FEATURE_SCHEMA_MISMATCH", "reference row/class schema")
        classes[row[class_at]] += 1
    require(count == 3010 and classes == {"0": 2843, "1": 167},
            "FEATURE_SCHEMA_MISMATCH", "reference sample/class counts")
    return {"schema": "dreamm-m1-reference-binding/1", "reference_sha256": REFERENCE_SHA256,
            "reference_bytes": len(reference), "reference_rows": count, "reference_columns": len(header),
            "model_sha256": profile.model_sha256, "ordered_feature_count": len(profile.names),
            "ordered_names_sha256": profile.names_sha256, "zero_filled_count": len(profile.zeroed),
            "zero_file_sha256": ZERO_FILE_SHA256, "class_counts": dict(classes),
            "scaler_fit_executed": False, "model_loaded": False}

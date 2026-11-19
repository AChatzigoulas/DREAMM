"""Versioned native boundary metadata, not an executor, scheduler or approval.

The authoritative queue owns job IDs, admission, leases, two slots and result
publication. Profiles cannot grant themselves scientific execution authority.
"""
from .capture import _json
from .identity import canonical, digest, require, sha

APPROVED_SCIENTIFIC_PROFILES = frozenset()  # owner approval + verified OS profile required
FIELDS = {"schema", "profile_id", "version", "scope", "python", "tool", "dependencies",
          "parameters", "inputs", "outputs", "resources", "containment", "modes"}


class ExecutionProfile:
    max_role_bytes = 64 * 1024**2

    def __init__(self, raw):
        require(isinstance(raw, bytes) and 0 < len(raw) <= 128 * 1024, "EXECUTION_PROFILE_INVALID", "profile byte bound")
        data = _json(raw)
        require(set(data) == FIELDS and data["schema"] == "dreamm-execution-profile/1"
                and isinstance(data["profile_id"], str) and 0 < len(data["profile_id"]) <= 64
                and type(data["version"]) is int and data["version"] >= 1
                and data["scope"] in ("parser", "preparation_charge", "preparation_structure", "features",
                                      "classifier_compatibility", "descriptors"),
                "EXECUTION_PROFILE_INVALID", "profile identity/scope")
        require(isinstance(data["modes"], list) and data["modes"] and len(set(data["modes"])) == len(data["modes"])
                and set(data["modes"]) <= {"M1", "M2", "M3", "M4", "M5"}, "EXECUTION_PROFILE_INVALID", "reference modes")
        for key in ("python", "tool", "dependencies", "parameters", "inputs", "outputs", "resources", "containment"):
            require(isinstance(data[key], dict), "EXECUTION_PROFILE_INVALID", "typed profile section")
        for name in ("inputs", "outputs"):
            require(data[name] and len(data[name]) <= 32, "EXECUTION_PROFILE_INVALID", "role bound")
            for role, item in data[name].items():
                require(isinstance(role, str) and role.replace("_", "").isalnum() and isinstance(item, dict)
                        and set(item) == {"schema", "sha256", "max_bytes"}
                        and isinstance(item["schema"], str) and type(item["max_bytes"]) is int
                        and 0 < item["max_bytes"] <= self.max_role_bytes, "EXECUTION_PROFILE_INVALID", "role schema/bounds")
                if item["sha256"] is not None:
                    sha(item["sha256"])
        self._canonical = canonical(data)

    @property
    def data(self):
        return _json(self._canonical)  # caller cannot poison internal profile

    @property
    def sha256(self):
        return digest(self._canonical)

    def readiness(self):
        d = self.data
        missing = []
        for section, key in (("python", "interpreter_sha256"), ("tool", "executable_sha256"),
                             ("tool", "version"), ("dependencies", "lock_sha256"),
                             ("parameters", "recipe_sha256"), ("containment", "verified_profile_sha256")):
            if section == "python" and d[section].get("required") is False:
                continue
            value = d[section].get(key)
            if value is None:
                missing.append(section + "." + key)
            elif key.endswith("sha256"):
                sha(value)
        if d["containment"].get("complete_tree_exit_required") is not True:
            missing.append("containment.complete_tree_exit_required")
        if d["resources"].get("whole_job_memory_enforced") is not True:
            missing.append("resources.whole_job_memory_enforced")
        for key in ("whole_job_memory_bytes", "CPU_threads", "process_limit", "wall_seconds", "output_budget_bytes"):
            value = d["resources"].get(key)
            if type(value) is not int or value <= 0:
                missing.append("resources." + key)
        if d["containment"].get("authority") != "external_authoritative_queue" or d["containment"].get("fallback") is not None:
            missing.append("containment.authority_or_fallback")
        return {"profile_sha256": self.sha256, "missing_verification": missing,
                "scientific_execution_approved": self.sha256 in APPROVED_SCIENTIFIC_PROFILES,
                "runnable": not missing and self.sha256 in APPROVED_SCIENTIFIC_PROFILES}

    def require_scientific_admission(self):
        require(self.data["scope"] != "parser" and self.readiness()["runnable"],
                "SCIENTIFIC_EXECUTION_UNAPPROVED", "profile is proposed/unverified or parser-only; no execution fallback")


def native_request(profile, *, job_id, attempt_id, mode, inputs):
    """Validate a data request for later queue-owned launch; does not launch."""
    import re
    require(isinstance(profile, ExecutionProfile) and re.fullmatch(r"[a-f0-9]{32}", job_id)
            and re.fullmatch(r"[a-f0-9]{32}", attempt_id) and mode in profile.data["modes"],
            "EXECUTION_PROFILE_INVALID", "existing queue identities/mode required")
    expected = profile.data["inputs"]
    require(set(inputs) == set(expected), "EXECUTION_PROFILE_INVALID", "input role set")
    for role, raw in inputs.items():
        require(isinstance(raw, bytes) and 0 < len(raw) <= expected[role]["max_bytes"], "RESOURCE_LIMIT", "input role bound")
        require(expected[role]["sha256"] is None or digest(raw) == expected[role]["sha256"],
                "EXECUTION_PROFILE_INVALID", "input hash differs")
    return {"schema": "dreamm-native-request/1", "job_id": job_id, "attempt_id": attempt_id,
            "mode": mode, "profile_sha256": profile.sha256,
            "inputs": {k: {"sha256": digest(v), "bytes": len(v)} for k, v in inputs.items()},
            "queue_controls": "external_authoritative_coordinator_only", "execution_authorized": False}


def verify_native_result(profile, request, result, artifacts):
    """Fail closed data-envelope check; never attests a caller's cleanup claim."""
    require(isinstance(profile, ExecutionProfile) and request["profile_sha256"] == profile.sha256
            and result.get("schema") == "dreamm-native-result/1"
            and all(result.get(k) == request[k] for k in ("job_id", "attempt_id", "profile_sha256")),
            "NATIVE_RESULT_INVALID", "stale/foreign result")
    require(type(result.get("exit_code")) is int and result["exit_code"] == 0
            and result.get("signal") is None and result.get("timed_out") is False,
            "NATIVE_RESULT_INVALID", "failed native result")
    expected = profile.data["outputs"]
    require(set(artifacts) == set(expected), "NATIVE_RESULT_INVALID", "output schemas/roles incomplete")
    for role, raw in artifacts.items():
        require(isinstance(raw, bytes) and 0 < len(raw) <= expected[role]["max_bytes"], "RESOURCE_LIMIT", "output bound")
        require(expected[role]["sha256"] is None or digest(raw) == expected[role]["sha256"],
                "NATIVE_RESULT_INVALID", "pinned output hash differs")
        require(result.get("artifacts", {}).get(role) == {"sha256": digest(raw), "bytes": len(raw), "schema": expected[role]["schema"]},
                "NATIVE_RESULT_INVALID", "output manifest mismatch")
    # No parser receipt, fabricated cleanup boolean, expiry or status query can
    # provide admission. Native launch/cleanup verification remains disabled.
    profile.require_scientific_admission()
    return result

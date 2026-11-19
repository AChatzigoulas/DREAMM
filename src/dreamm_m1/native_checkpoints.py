"""Scientific checkpoint validation. Transport and runtime attestations come from the coordinator."""
from dataclasses import dataclass
import re

from .extraction import PYTHON_COLUMNS, conservation_scores
from .features import number
from .hotspot_benchmarks import HH_LEGACY_BINARY_SHA256
from .identity import canonical, digest, require, sha
from .pdb_evidence import read_python_features
from .rocky_database import FINGERPRINT


@dataclass(frozen=True)
class M1Checkpoints:
    # An admitted process receives a narrow claim-bound transport, never the
    # AttemptCheckpoints object or write access to the coordinator directory.
    transport: object
    runtime_sha256: str
    database_sha256: str
    deadline: object

    def __post_init__(self):
        sha(self.runtime_sha256)
        require(self.database_sha256 == FINGERPRINT, "CHECKPOINT_INVALID", "unexpected UniClust30 fingerprint")

    def seconds(self, maximum):
        return self.deadline.stage(maximum)

    def inputs(self, context):
        return {**context, "runtime": self.runtime_sha256, "uniclust30": self.database_sha256,
                "hhblits": HH_LEGACY_BINARY_SHA256}

    def load(self, stage, inputs, names):
        return self.transport.load(stage, self.inputs(inputs), names)

    def commit(self, stage, inputs, files):
        self.transport.commit(stage, self.inputs(inputs), files)


HH_FILES = ("input.fasta", "query.json", "output.hhm", "input.hhr", "conservation.json")


def conservation_checkpoint(fasta, query, hhm, hhr):
    """Recompute the identical conversion and residue map from the raw HHM."""
    match = re.search(rb"^Match_columns[ \t]+([0-9]{1,5})[ \t]*\r?$", hhr, re.MULTILINE)
    require(match is not None and int(match[1]) == len(query["sequence"]), "CHECKPOINT_INVALID",
            "HHblits report does not cover the exact query length")
    scores = conservation_scores(hhm, query["sequence"])
    receipt = [{"residue_uid": position["residue_uid"], "position": position["position"], "value": score}
               for position, score in zip(query["positions"], scores)]
    return scores, dict(zip(HH_FILES, (fasta, canonical(query), hhm, hhr, canonical(receipt))))


def validate_python_features(raw, adapter, dssp, conservation):
    """Validate order, full schema and exact direct Conservation scores before reuse."""
    rows = read_python_features(raw, adapter)
    require(tuple(row.locator for row in rows) == tuple(dssp), "CHECKPOINT_INVALID",
            "deterministic feature residue order or coverage changed")
    for row in rows:
        base = dssp[row.locator]
        require(tuple(row.values) == PYTHON_COLUMNS and row.aa == base["Amino acid"]
                and row.values["Secondary structure"] == base["Secondary structure"]
                and number(row.values["Conservation score"]) == conservation[row.locator],
                "CHECKPOINT_INVALID", "deterministic feature schema or reference values changed")
        for key in PYTHON_COLUMNS[3:]:
            number(row.values[key])
    return raw

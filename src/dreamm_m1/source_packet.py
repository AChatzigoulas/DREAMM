"""Transfer the already parsed frozen 1RLW to the legacy science interpreter."""
from pathlib import Path
from types import MappingProxyType

from .capture import _json, _parents, _read
from .identity import SourceResidue, digest, require
from .mmcif_input import CIF_1RLW_SHA256, CifAtom, CifSource, CifValue


# From the approved Gemmi run m1-gemmi-run-20-compiled-final, receipts/.
_HASHES = {
    "source.cif": CIF_1RLW_SHA256,
    "source_atoms.json": "4824d19024e74b8969ccf6e1364c3f94b13f4c198ff0b66b7211cfbd52b48bba",
    "source_models.json": "a8def17e4484dc69acb96f0895b729010b790d76e96c6d89b1823d553f55602e",
    "source_residues.json": "b486cb2a48fab139b4e34379b52a04723a4623c8c616cb9f8e4f8f641436e10f",
    "intake.json": "51cc11c385af510f9ed58fec0e361d85d38dabe11803a8e5789947a09a15bfd2",
}


def load_1rlw_source_packet(directory):
    directory = Path(directory).absolute()
    _parents(directory)
    files = {}
    for name, expected in _HASHES.items():
        raw = _read(directory / name)
        require(digest(raw) == expected, "SOURCE_INVALID", "frozen 1RLW parser receipt " + name)
        files[name] = raw
    intake = _json(files["intake.json"])
    residues = {uid: SourceResidue(**row) for uid, row in _json(files["source_residues.json"]).items()}
    models = _json(files["source_models.json"])["models"]
    atoms = tuple(CifAtom(row["atom_uid"], row["atom_site_row_index"], row["model_index"], residues[row["residue_uid"]],
                          MappingProxyType({k: CifValue(**v) for k, v in row["fields"].items()}))
                  for row in _json(files["source_atoms.json"]))
    return CifSource(files["source.cif"], intake["source"]["block_name"], tuple(intake["source"]["atom_site_tags_in_order"]),
                     atoms, tuple(row["source_model_id"] for row in models), MappingProxyType(intake["parser_runtime"]))

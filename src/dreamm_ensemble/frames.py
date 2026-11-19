"""Ordered frame admission and an exact, checked mmCIF-to-PDB projection."""
from decimal import Decimal, InvalidOperation
import re

from dreamm_m1.identity import canonical, digest
from dreamm_m1.pdb_input import read_pdb_source


def frame_ids(mode, source_count, additional=None):
    from dreamm_m1.local_budget import model_count
    if type(source_count) is not int or not 1 <= source_count <= model_count():
        raise ValueError("unsupported source frame count")
    if mode in ("M3", "M4") and additional is None:
        return tuple(range(source_count))
    if mode == "M5" and type(additional) is int and 1 <= additional <= 50:
        return tuple(range(additional + 1))
    raise ValueError("invalid mode or generation request")


def completed_frames(expected, observed):
    observed = tuple(observed)
    if observed != tuple(expected):
        raise ValueError("missing, duplicate or out-of-order frame receipt")
    return observed


def groups(source):
    frames = [[] for _ in source.model_ids]
    for atom in source.atoms:
        frames[atom.model_index].append(atom)
    return frames


def _residue_key(atom):
    return (atom.locator.chain, atom.locator.resnum, atom.locator.insertion,
            atom.residue.source_resname)


def _atom_key(atom):
    return (atom.record, *_residue_key(atom), atom.atom_name_raw.strip(),
            atom.altloc_raw, atom.element_raw.strip())


def _ca_keys(atoms):
    keys = [_residue_key(atom) for atom in atoms
            if atom.record == "ATOM" and atom.atom_name_raw.strip() == "CA"]
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("missing or ambiguous C-alpha identity")
    return keys


def pdb_manifest(source, *, selected_ca=None):
    result = []
    for index, atoms in enumerate(groups(source)):
        ca_keys = _ca_keys(atoms) if selected_ca is None else selected_ca[index]
        result.append({"frame_index": index, "source_model_id": source.model_ids[index],
                       "atoms": len(atoms), "ca_residues": len(ca_keys),
                       "chains": sorted({k[0] for k in ca_keys}),
                       "ca_identity_sha256": digest(canonical(ca_keys)),
                       "atom_identity_sha256": digest(canonical([_atom_key(a) for a in atoms])),
                       "coordinates_sha256": digest(canonical([a.xyz_raw for a in atoms]))})
    return result


def first_pdb_frame(source):
    """Public M4 one-model derivative; preserve atom/TER record bytes and source model ID."""
    records = source.original.splitlines(keepends=True)
    atoms = source.reference_atoms
    end = atoms[-1].line
    if end < len(records) and records[end].startswith(b"TER "):
        end += 1
    body = b"".join(records[atoms[0].line - 1:end])
    model = source.model_ids[0]
    if model is not None:
        body = f"MODEL     {model:>4}\n".encode("ascii") + body + b"ENDMDL\n"
    raw = body + b"END\n"
    parsed = read_pdb_source(raw, expected_sha256=digest(raw))
    payload = lambda a: (_atom_key(a), a.serial_raw, a.xyz_raw, a.occupancy_raw, a.bfactor_raw)
    if parsed.model_ids != (model,) or list(map(payload, parsed.atoms)) != list(map(payload, atoms)):
        raise ValueError("first-frame derivative changed a source atom")
    return raw


def _field(value, pattern, kind):
    if value is None or re.fullmatch(pattern, value, flags=re.ASCII) is None:
        raise ValueError(f"unrepresentable PDB {kind}")
    return value


def _decimal_field(value, width, decimals):
    if value is None or len(value) > 32:
        raise ValueError("missing or unrepresentable PDB numeric field")
    try:
        n = Decimal(value)
        if not n.is_finite() or (n and not -decimals <= n.adjusted() < width - decimals - 1):
            raise ValueError("PDB projection would round or overflow a numeric field")
        formatted = f"{n:{width}.{decimals}f}"
        if len(formatted) != width or Decimal(formatted) != n:
            raise ValueError("PDB projection would round or overflow a numeric field")
        return formatted
    except InvalidOperation as exc:
        raise ValueError("invalid PDB numeric field") from exc


def _pdb_row(atom, serial):
    def field(name):
        return atom.field(name).value

    chain = _field(field("auth_asym_id"), r"[A-Za-z0-9]", "chain")
    seq = _field(field("auth_seq_id"), r"0|-?[1-9][0-9]*", "residue number")
    if len(seq) > 4 or serial > 99999:
        raise ValueError("PDB identifier overflow")
    resname = _field(field("auth_comp_id"), r"[A-Z0-9]{1,3}", "residue name")
    atom_name = _field(field("auth_atom_id"), r"[A-Za-z0-9'\"*]{1,4}", "atom name")
    element = _field(field("type_symbol"), r"[A-Za-z]{1,2}", "element")
    alt = _field(field("label_alt_id") or " ", r"[A-Za-z0-9 ]", "alternate location")
    ins = _field(field("pdbx_PDB_ins_code") or " ", r"[A-Za-z0-9 ]", "insertion code")
    charge = field("pdbx_formal_charge") if "pdbx_formal_charge" in atom.fields else None
    if charge not in (None, "0"):
        charge = _field(charge, r"-?[1-9]|\+[1-9]", "charge")
        charge = f"{abs(int(charge))}{'+' if int(charge) > 0 else '-'}"
    else:
        charge = "  "
    aligned_name = (f" {atom_name:<3}" if len(atom_name) < 4 and len(element) == 1
                    and atom_name[0].isalpha() else f"{atom_name:<4}")
    xyz = "".join(_decimal_field(field(f"Cartn_{a}"), 8, 3) for a in "xyz")
    occupancy = _decimal_field(field("occupancy"), 6, 2)
    bfactor = _decimal_field(field("B_iso_or_equiv"), 6, 2)
    return (f"{atom.record:<6}{serial:5} {aligned_name}{alt}{resname:>3} {chain}"
            f"{seq:>4}{ins}   {xyz}{occupancy}{bfactor}          {element:>2}{charge}\n")


def project_mmcif(source):
    """Tool projection only. Original CIF retains auth/label tokens and source model IDs."""
    lines = []
    source_groups = groups(source)
    for model, atoms in zip(source.model_ids, source_groups):
        _field(model, r"[1-9][0-9]{0,3}", "model number")
        lines.append(f"MODEL     {model:>4}\n")
        last = None
        serial = 0
        for atom in atoms:
            if last is not None and last[21] != atom.residue.auth_chain:
                serial += 1
                lines.append(_termination(last, serial))
            serial += 1
            last = _pdb_row(atom, serial)
            lines.append(last)
        lines.extend((_termination(last, serial + 1), "ENDMDL\n"))
    raw = ("".join(lines) + "END\n").encode("ascii")
    pdb = read_pdb_source(raw, expected_sha256=digest(raw))
    completed_frames(source.model_ids, pdb.model_ids)
    crosswalk = []
    for source_atoms, pdb_atoms in zip(source_groups, groups(pdb)):
        if len(source_atoms) != len(pdb_atoms):
            raise ValueError("projection lost atoms")
        pairs = []
        for a, b in zip(source_atoms, pdb_atoms):
            def field(name):
                return a.field(name).value

            key = (a.record, a.residue.auth_chain, a.residue.auth_seq,
                   # The PDB reader retains the entire fixed-width component
                   # field, including padding for components such as CD.
                   field("pdbx_PDB_ins_code") or " ", a.residue.source_resname.rjust(3),
                   field("auth_atom_id"), field("label_alt_id") or " ", field("type_symbol"))
            numeric = [field(f"Cartn_{ax}") for ax in "xyz"] + [field("occupancy"), field("B_iso_or_equiv")]
            if key != _atom_key(b) or tuple(map(Decimal, numeric)) != tuple(map(
                    Decimal, (*b.xyz_raw, b.occupancy_raw, b.bfactor_raw))):
                raise ValueError("projection failed exact atom identity or coordinate check")
            pairs.append([a.uid, b.uid])
        crosswalk.append(digest(canonical(pairs)))
    return pdb, crosswalk


def _termination(last, serial):
    if last is None or serial > 99999:
        raise ValueError("empty or overflowing PDB model")
    return f"TER   {serial:5}      {last[17:27]}\n"


def compare_pdb(left, right):
    """Join by full author atom identity and original model ID, then compare values."""
    completed_frames(left.model_ids, right.model_ids)
    results = []
    for l_frame, r_frame in zip(groups(left), groups(right)):
        l = {_atom_key(a): a for a in l_frame}
        r = {_atom_key(a): a for a in r_frame}
        if len(l) != len(l_frame) or len(r) != len(r_frame):
            raise ValueError("ambiguous atom identity without segment")
        common = l.keys() & r.keys()
        changes = {"xyz": 0, "occupancy": 0, "bfactor": 0}
        for k in common:
            for name, x, y in zip(changes,
                    (l[k].xyz_raw, (l[k].occupancy_raw,), (l[k].bfactor_raw,)),
                    (r[k].xyz_raw, (r[k].occupancy_raw,), (r[k].bfactor_raw,))):
                changes[name] += tuple(Decimal(s) if s.strip() else None for s in x) != tuple(
                    Decimal(s) if s.strip() else None for s in y)
        results.append({"same_order": tuple(l) == tuple(r),
                        "same_ca_order": _ca_keys(l_frame) == _ca_keys(r_frame),
                        "common_atoms": len(common), "left_only": len(l.keys() - r.keys()),
                        "right_only": len(r.keys() - l.keys()), "changed": changes})
    return results

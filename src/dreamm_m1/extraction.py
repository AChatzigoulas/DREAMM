"""Identity-bound legacy feature assembly for the single-reference M1 worker."""
import math
import re

from .features import number
from .identity import AA, MAX_BYTES, Locator, require


DSSP_COLUMNS = ("Phi", "Psi", "TCO", "KAPPA", "ALPHA")
SASA_COLUMNS = ("all_atoms_abs", "side_chain_abs", "main_chain_abs", "all_polar_abs", "non_polar_abs")
HYDRO_COLUMNS = ("Hydrophobicity", "Hydrophobicity2", "Hydrophobicity3", "Hydrophobicity4")
NEIGHBOR_AA = "ARNDCEQGHILKMFPSTWYV"
BASE_COLUMNS = ("resnum", "Amino acid", "Secondary structure", *DSSP_COLUMNS, *SASA_COLUMNS,
                "res_depth", "ca_depth", "GNM", "ANM", "Conservation score", "Charges",
                *HYDRO_COLUMNS, "Radius of gyration")
MEAN_COLUMNS = BASE_COLUMNS[3:]
PYTHON_COLUMNS = (*BASE_COLUMNS, *("neigh_" + aa for aa in NEIGHBOR_AA),
                  *("mean_" + name for name in MEAN_COLUMNS), "#Neighbors")
WW_OCTANOL = dict(zip(NEIGHBOR_AA, (.5, 1.81, .85, 3.64, -.02, 3.63, .77, 1.15, 2.33,
                                    -1.12, -1.25, 2.80, -.67, -1.71, .14, .46, .25, -2.09, -.71, -.46)))
WW_INTERFACE = dict(zip(NEIGHBOR_AA, (.17, .81, .42, 1.23, -.24, 2.02, .58, .01, .96,
                                      -.31, -.56, .99, -.23, -1.13, .45, .13, .14, -1.85, -.94, .07)))


def hydrophobicity(aa, charge):
    """Legacy integer-charge test and both positive-value clips (LF601–637)."""
    special = {"H": (.11, .17), "E": (.11, -.01), "D": (.43, -.07)}
    first, second = (special[aa] if aa in special and int(number(charge)) == 0
                     else (WW_OCTANOL[aa], WW_INTERFACE[aa]))
    return dict(zip(HYDRO_COLUMNS, (first, min(first, 0.0), second, min(second, 0.0))))


def bound_records(records, bindings, stage):
    """Bind a tool's chain, residue and insertion identifiers without segment guesses."""
    index = {(loc.chain, int(loc.resnum), loc.insertion): loc for loc in bindings}
    require(len(index) == len(bindings), "IDENTITY_AMBIGUOUS", stage + " source locators")
    result = {}
    for chain, resnum, insertion, resname, values in records:
        locator = index.get((chain, int(resnum), insertion or " "))
        require(locator is not None and locator not in result and resname == bindings[locator].scientific_resname,
                "FEATURE_MAPPING_UNPROVEN", stage + " unexpected, duplicate or misnamed residue")
        result[locator] = values
    require(set(result) == set(bindings), "FEATURE_MAPPING_UNPROVEN", stage + " incomplete residue coverage")
    return result


def dssp_features(properties, bindings):
    names = {(loc.chain, int(loc.resnum), loc.insertion): res.scientific_resname for loc, res in bindings.items()}
    records = []
    for (chain, (hetero, resnum, insertion)), values in properties.items():
        resname = names.get((chain, resnum, insertion))
        require(hetero == " " and len(values) == 14 and AA.get(resname) == values[1],
                "FEATURE_MAPPING_UNPROVEN", "Biopython 1.78 DSSP identity/tuple layout")
        secondary = "C" if values[2] == "-" else values[2]
        require(secondary in tuple("BCEGHIST"), "FEATURE_SCHEMA_MISMATCH", "DSSP secondary structure")
        # LF455–475,518 use these positions, including the historical misnomers.
        row = {"resnum": resnum, "Amino acid": values[1], "Secondary structure": secondary}
        row.update(zip(DSSP_COLUMNS, values[4:9]))
        records.append((chain, resnum, insertion, resname, row))
    return bound_records(records, bindings, "DSSP")


def rsa_features(raw, bindings):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, "RESOURCE_LIMIT", "FreeSASA RSA bytes")
    records = []
    for line in raw.decode("ascii").splitlines():
        if line.startswith("RES "):
            require(len(line) >= 74, "FEATURE_SCHEMA_MISMATCH", "short RSA residue record")
            fields = (line[16:22], line[29:35], line[42:48], line[68:74], line[55:61])
            records.append((line[8], int(line[9:13]), line[13], line[4:7],
                            dict(zip(SASA_COLUMNS, map(number, fields)))))
    return bound_records(records, bindings, "FreeSASA")


def conservation_scores(raw, sequence):
    """Read selected residue emissions with the legacy HH-suite log conversion."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, "RESOURCE_LIMIT", "HHM byte bound")
    lines = raw.decode("ascii").splitlines()
    headers = [i for i, line in enumerate(lines) if line.startswith("HMM ")]
    require(len(headers) == 1 and lines[-1].strip() == "//", "FEATURE_SCHEMA_MISMATCH", "HHM header/terminator")
    header = headers[0]
    columns = lines[header].split()[1:]
    require(columns == list("ACDEFGHIKLMNPQRSTVWY"), "FEATURE_SCHEMA_MISMATCH", "HHM amino-acid columns")
    scores = []
    for line in lines[header + 2:-1]:
        fields = line.split()
        if not fields or len(fields[0]) != 1 or not fields[0].isalpha():
            continue
        position = len(scores)
        require(len(fields) in (22, 23) and position < len(sequence) and fields[0] == sequence[position]
                and fields[1] == str(position + 1), "FEATURE_MAPPING_UNPROVEN", "HHM query position/symbol")
        emissions = fields[2:22]
        require(all(token == "*" or re.fullmatch(r"-?\d{1,6}", token) for token in emissions),
                "FEATURE_SCHEMA_MISMATCH", "HHM emission token")
        token = emissions[columns.index(fields[0])]
        value = 0.0 if token == "*" else 2 ** (-int(token) / 1000)
        require(math.isfinite(value), "FEATURE_SCHEMA_MISMATCH", "nonfinite HHM emission")
        scores.append(value)
    require(len(scores) == len(sequence), "FEATURE_MAPPING_UNPROVEN", "HHM query coverage")
    return tuple(scores)


def neighbor_counts(rows):
    unique = {aa: set() for aa in NEIGHBOR_AA}
    for row in rows:
        unique[row["Amino acid"]].add(row["resnum"])
    return {"neigh_" + aa: float(len(numbers)) for aa, numbers in unique.items()}


def assemble_csv(base_rows, ca, distances, pd):
    """Produce the legacy CSV, using the admitted pandas/MDAnalysis operations.

    DSSP insertion order is the row order for means and for the sequence query.
    The caller supplies the pre-preparation CA coordinates in that same order.
    """
    locators = list(base_rows)
    rows = list(base_rows.values())
    require(len(rows) == len(ca) and all(set(row) == set(BASE_COLUMNS) for row in rows),
            "FEATURE_SCHEMA_MISMATCH", "complete native Python feature inputs")
    index = pd.MultiIndex.from_tuples([(loc.chain, (" ", int(loc.resnum), loc.insertion)) for loc in locators])
    base = pd.DataFrame(rows, index=index, columns=BASE_COLUMNS)
    # The mixed-type DSSP transpose produces object columns before reduction.
    for column in DSSP_COLUMNS:
        base[column] = base[column].astype(object)
    neighbors = []
    for i in range(len(locators)):
        nearby = (distances(ca[i:i+1], ca).reshape(-1) < 7).nonzero()[0]
        require(i in nearby, "FEATURE_MAPPING_UNPROVEN", "missing self-neighbor")
        subset = base.iloc[nearby]
        values = neighbor_counts([rows[j] for j in nearby])
        values.update(("mean_" + col, subset[col].mean()) for col in MEAN_COLUMNS)
        values["#Neighbors"] = float(len(nearby))
        neighbors.append(values)
    assembled = pd.concat([base, pd.DataFrame(neighbors, index=index)], axis=1)
    return assembled.loc[:, list(PYTHON_COLUMNS)].to_csv().encode("utf-8")

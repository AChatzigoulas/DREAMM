"""Versioned modern ensemble adapter derived from validated DREAMM functions.

Original scientific reductions and clustering calls are retained. Runtime
admission, separate ESSA execution and source-independent paths live in
ensemble_local.py. This module never imports an OLD-VM installation.
"""
from collections import defaultdict
from contextlib import contextmanager
from itertools import chain
import json, math, os, resource, time, zipfile
from pathlib import Path

INPUT=None
EXPECTED_POCKETS={}
RANK_COLUMNS = ("conformation_#_pocket_#", "total_conformations", "membrane-penetrating_residues",
                "dist_surf_mean", "dist_surf_std", "dist_center_mean", "dist_center_std", "highest_score",
                "highest_score_conf", "site_rank_mean", "site_rank_std", "center_x_mean", "center_x_std",
                "center_y_mean", "center_y_std", "center_z_mean", "center_z_std",
                "conservation_mean", "conservation_std", "ESSA_mean", "ESSA_std")
FILTER_COLUMNS = ("residue", "dist_surf", "site_rank", "score", "dist_center", "center_x", "center_y", "center_z",
                  "conformation", "res_id", "surf_atoms")

@contextmanager
def measured(stage, spans):
    a = resource.getrusage(resource.RUSAGE_SELF)
    b = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    yield
    c = resource.getrusage(resource.RUSAGE_SELF)
    d = resource.getrusage(resource.RUSAGE_CHILDREN)
    spans.append({"stage": stage, "wall_s": time.monotonic() - start,
                  "cpu_s": (c.ru_utime + c.ru_stime - a.ru_utime - a.ru_stime) +
                           (d.ru_utime + d.ru_stime - b.ru_utime - b.ru_stime),
                  "self_lifetime_peak_rss_kib": c.ru_maxrss, "child_lifetime_peak_rss_kib": d.ru_maxrss,
                  "block_reads": c.ru_inblock + d.ru_inblock - a.ru_inblock - b.ru_inblock,
                  "block_writes": c.ru_oublock + d.ru_oublock - a.ru_oublock - b.ru_oublock})

def frame_geometry(frame, ref):
    from MDAnalysis import Universe
    from dreamm_ensemble.p2rank_csv import geometry_by_serial, prepared_serials
    from dreamm_m1.identity import AA, Locator

    file = INPUT / "frames" / frame["name"]
    atoms = prepared_serials(file.read_bytes(), frame["sha256"])
    groups, names, identities, ca = defaultdict(list), {}, {}, {}
    geometries = []
    for atom in Universe(str(file)).select_atoms("protein"):
        serial = int(atom.id)
        segment = atom.segid.strip()
        p = atoms[serial]
        if not p.locator.segment and segment == atom.chainID:
            segment = ""
        loc = Locator(atom.chainID, str(atom.resid), atom.icode or " ", segment)
        if atom.resname not in AA:
            raise ValueError("unexpected prepared residue identity")
        key = (atom.record_type, loc, atom.resname, atom.name, atom.altLoc or " ", atom.element)
        geometries.append((serial, int(atom.index), key, tuple(map(float, atom.position))))
        number = str(loc.residue_label)
        label = loc.chain + "_" + number
        if label not in ref or AA[atom.resname] != ref[label]["aa"]:
            raise ValueError("ensemble residue is not bound to the reference residue")
        groups[label].append(serial)
        names[loc.chain, number] = atom.resname
        identities[label] = (loc.chain, number)
        if atom.name == "CA":
            if label in ca:
                raise ValueError("duplicate C-alpha for mapped ensemble residue")
            ca[label] = atom.resname
    coords, positional = geometry_by_serial(atoms, geometries)
    if set(groups) != set(ref) or set(ca) != set(ref) or coords.keys() != atoms.keys():
        raise ValueError("partial frame-to-reference correspondence")
    selected = {label: tuple(groups[label]) for label, row in ref.items() if row["membrane"]}
    return {"file": file, "coordinates": coords, "groups": dict(groups), "ca": ca, "names": names,
            "identities": identities, "positional": positional, "selected": selected}

def filter_frame(profile, frame, geometry):
    import numpy as np
    from MDAnalysis.lib.distances import distance_array
    from dreamm_ensemble.p2rank_csv import read_predictions, read_residue_scores
    from dreamm_ensemble.pockets import batched_filter, scalar_filter
    from dreamm_m1.identity import canonical

    file = INPUT / "raw" / profile / frame["name"]
    pred = read_predictions(file.with_name(file.name + "_predictions.csv").read_bytes(),
                            coordinates=geometry["coordinates"], residue_ids=geometry["identities"], version="2.5.1")
    read_residue_scores(file.with_name(file.name + "_residues.csv").read_bytes(),
                        identities=geometry["names"], ranks={p.rank for p in pred})

    def kernel(a, b):
        return distance_array(np.asarray(a), np.asarray(b), backend="serial")

    args = (geometry["coordinates"], geometry["selected"], tuple(p.for_distance() for p in pred), kernel)
    return pred, args, scalar_filter, batched_filter, canonical

def rankings(data, pd):
    import numpy as np
    if data.empty:
        return pd.DataFrame(columns=RANK_COLUMNS), []
    grouped = data.groupby(["conformation", "class"]).agg({"residue": "unique", "dist_surf": ["min"],
        "dist_center": ["min"], "score": ["max"], "site_rank": ["mean"], "center_x": ["mean"],
        "center_y": ["mean"], "center_z": ["mean"], "conservation": ["mean"], "ESSA": ["mean"]}).reset_index()
    grouped.columns = [col[0] for col in grouped.columns]
    display = "conformation_#_pocket_#"
    grouped[display] = grouped["conformation"] + "_pocket_" + grouped["site_rank"].astype(int).astype(str)
    out = grouped.groupby("class").agg({display: ["unique", "nunique"],
        "residue": lambda x: sorted(set(chain.from_iterable(x))), "dist_surf": ["mean", "std"],
        "dist_center": ["mean", "std"], "score": ["max", "idxmax"], "site_rank": ["mean", "std"],
        "center_x": ["mean", "std"], "center_y": ["mean", "std"], "center_z": ["mean", "std"],
        "conservation": ["mean", "std"], "ESSA": ["mean", "std"]}).sort_values([("score", "max")],
            ascending=False).fillna(0)
    membership = []
    for label, row in out.iterrows():
        label = int(label)
        group = data[data["class"] == label]
        frames = sorted(set(group["conformation"]))
        if len(frames) != row[(display, "nunique")]:
            raise ValueError("legacy occupancy disagrees with explicit distinct frames")
        highest = grouped.iloc[int(row[("score", "idxmax")])]["conformation"]
        if highest not in frames:
            raise ValueError("legacy score tie or highest frame is unresolved")
        membership.append({"class": label, "frames": frames,
                           "pockets": [list(pair) for pair in sorted(set(zip(group["conformation"], group["site_rank"])) )]})
    out[("score", "idxmax")] = [grouped.iloc[int(i)]["conformation"] for i in out[("score", "idxmax")]]
    out[(display, "unique")] = out[(display, "unique")].map(lambda v: np.asarray(list(v), dtype=object))
    out.columns = RANK_COLUMNS
    out.index = ["Binding site %d" % i for i in range(1, len(out) + 1)]
    return out, membership

def write_excel(path, data):
    from openpyxl import load_workbook
    from openpyxl.compat.strings import safe_string
    import numbers

    data.to_excel(str(path), engine="openpyxl")
    workbook = load_workbook(str(path), read_only=True, data_only=True)
    try:
        rows = list(workbook.active.values)
        if len(rows) != len(data) + 1 or tuple(rows[0][1:]) != tuple(data.columns):
            raise ValueError("XLSX shape or schema differs")
        for actual, row in zip(rows[1:], data.itertuples(name=None)):
            expected = tuple(float(safe_string(x)) if isinstance(x, numbers.Real)
                             else x if isinstance(x, str) else str(x) for x in row)
            if actual != expected:
                raise ValueError("XLSX cell round-trip differs")
    finally:
        workbook.close()

def generate_visualization(folder, frame, geometry, pockets, profile, accepted):
    name = frame["name"]
    points = name + "_points.pdb.gz"
    raw = INPUT / "raw" / profile
    data = folder / "visualizations" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / points).write_bytes((raw / points).read_bytes())
    (data / name).write_bytes(geometry["file"].read_bytes())
    membrane = geometry["selected"]
    commands = ["load data/" + name + ", protein", "show cartoon, protein",
                "load data/" + points + ", points", "hide everything, points"]
    if membrane:
        commands.extend(["select dreamm_membrane, protein and (" + " or ".join("(chain " + key.split("_")[0] + " and resi " + key.split("_")[1] + ")" for key in geometry["selected"]) + ")",
                         "show sticks, dreamm_membrane"])
    for pocket in pockets:
        if pocket.rank in accepted:
            index = str(pocket.rank)
            commands.extend(["select dreamm_pocket_" + index + ", points and resi " + index,
                             "show spheres, dreamm_pocket_" + index])
    commands.append("zoom protein")
    (folder / "visualizations" / (name + ".pml")).write_text("\n".join(commands) + "\n", encoding="ascii")

def evaluate(profile, frames, geometries, ref, essa_cache, spans, target):
    import numpy as np
    import pandas as pd
    from sklearn.cluster import KMeans, MeanShift
    from sklearn.metrics import silhouette_score
    from dreamm_ensemble.cluster_replay import Association, fit_legacy, metric_values
    from dreamm_m1.identity import canonical, digest

    profile_dir = target / profile
    profile_dir.mkdir()
    by_frame, rows, detailed = {}, [], []
    # Archived p2rank.py assigned frames to strided Pool chunks and concatenated in chunk order.
    cpus = math.ceil(len(frames) / math.ceil(len(frames) / os.cpu_count()))
    order = [i for offset in range(cpus) for i in range(offset, len(frames), cpus)]
    for i in order:
        frame, geo = frames[i], geometries[i]
        with measured(profile + ".bind_pockets", spans):
            pockets, args, scalar, batch, serialize = filter_frame(profile, frame, geo)
        with measured(profile + ".filter.scalar", spans):
            original = scalar(*args)
        with measured(profile + ".filter.batched", spans):
            filtered = batch(*args)
        if serialize(original) != serialize(filtered):
            raise ValueError("scalar and batched reference-membrane associations differ")
        indexed = {p.rank: p for p in pockets}
        accepted = sorted({item[1] for item in filtered})
        by_frame[i] = {"frame": i, "name": frame["name"], "pockets": len(pockets), "accepted": accepted,
                       "association_rows": len(filtered), "association_sha256": digest(serialize(filtered))}
        if filtered and i not in essa_cache:
            with measured("essa.frame-%02d" % (i + 1), spans):
                essa_cache[i] = essa(frame, geo)
        with measured(profile + ".interface_metrics", spans):
            for residue, rank, score, distance, center in filtered:
                pocket = indexed[rank]
                tokens = pocket.residue_tokens
                conservation = pd.Series(metric_values(((k, v["conservation"]) for k, v in ref.items()), tokens)).mean()
                sensitivity = pd.Series(metric_values(essa_cache[i].items(), tokens)).median()
                rows.append([residue, distance, rank, score, center, *map(float, pocket.center), frame["name"],
                             " ".join(tokens), " ".join(map(str, pocket.surface_serials)),
                             float(conservation), float(sensitivity)])
                detailed.append({"frame": i, "pocket_rank": rank, "residue": residue,
                                 "reference_uid": ref[residue]["uid"], "probability": str(pocket.probability)})
        with measured(profile + ".visualization", spans):
            generate_visualization(profile_dir, frame, geo, pockets, profile, accepted)
        for role in ("predictions", "residues"):
            name = frame["name"] + "_" + role + ".csv"
            (profile_dir / name).write_bytes((INPUT / "raw" / profile / name).read_bytes())
    if sum(r["pockets"] for r in by_frame.values()) != EXPECTED_POCKETS[profile]:
        raise ValueError("frozen P2Rank pocket total differs")
    data = pd.DataFrame(rows, columns=FILTER_COLUMNS + ("conservation", "ESSA"))
    fits = []
    if rows:
        associations = [Association(d["frame"], d["pocket_rank"], d["residue"], tuple(row[5:8]))
                        for row, d in zip(rows, detailed)]
        matrix = lambda v: pd.DataFrame(v, columns=["center_x", "center_y", "center_z"])
        for i in range(2):
            state = np.random.get_state()
            rng = digest(canonical([state[0], state[1].tolist(), state[2], state[3], state[4]]))
            with measured(profile + ".clustering.run%d" % (i + 1), spans):
                fit = fit_legacy(associations, kmeans=KMeans, meanshift=MeanShift,
                                 silhouette=silhouette_score, matrix=matrix)
            fits.append({**fit, "initial_numpy_rng_state_sha256": rng})
        data["class"] = fits[0]["labels"]
        for d, label in zip(detailed, fits[0]["labels"]):
            d["class"] = label
    else:
        data["class"] = pd.Series(dtype="int64")
    with measured(profile + ".ranking_and_exports", spans):
        ranked, membership = rankings(data, pd)
        sites = []
        for index, (_, row) in enumerate(ranked.iterrows(), 1):
            values = {k: (list(v) if isinstance(v, (np.ndarray, pd.api.extensions.ExtensionArray)) else v.item() if isinstance(v, np.generic) else v)
                      for k, v in row.items()}
            sites.append({"binding_site": index, **membership[index - 1], "frame_denominator": len(frames), **values})
        data.to_csv(str(profile_dir / "interface-pockets.csv"))
        ranked.to_csv(str(profile_dir / "binding-sites.csv"))
        write_excel(profile_dir / "interface-pockets.xlsx", data)
        write_excel(profile_dir / "binding-sites.xlsx", ranked)
        status = "scientific_positive" if sites else "scientific_empty"
        lines = ["%d binding sites were found in the predicted protein-membrane interfaces." % len(sites)]
        for site in sites:
            lines.append(("Binding site {binding_site} is found in {total_conformations}/{frame_denominator} "
                          "conformations near {membrane-penetrating_residues}. Highest P2Rank score {highest_score:.3f}. "
                          "Mean surface distance {dist_surf_mean:.2f} ± {dist_surf_std:.2f} Å. "
                          "Mean center distance {dist_center_mean:.2f} ± {dist_center_std:.2f} Å. "
                          "Mean Conservation {conservation_mean:.2f} ± {conservation_std:.2f}. "
                          "Mean ESSA {ESSA_mean:.2f} ± {ESSA_std:.2f}.").format_map(site))
        (profile_dir / "results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = {"status": status, "frames": [by_frame[i] for i in range(len(frames))], "legacy_row_order": order,
                  "interface_associations": detailed, "fit_attempts": fits, "sites": sites,
                  "fit_partitions_equal_up_to_label_permutation":
                      fits[0]["partitions"] == fits[1]["partitions"] if fits else None}
        (profile_dir / "profile-result.json").write_bytes(canonical(report))
    return report

def bundle(target):
    from dreamm_m1.identity import canonical, digest
    index = {p.relative_to(target).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p.read_bytes())}
             for p in sorted(target.rglob("*")) if p.is_file()}
    index_path = target / "zip-members.json"
    index_path.write_bytes(canonical(index))
    index["zip-members.json"] = {"bytes": index_path.stat().st_size, "sha256": digest(index_path.read_bytes())}
    zip_path = target.parent / "DREAMM-ensemble.zip"
    with zipfile.ZipFile(str(zip_path), mode="x", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name in index:
            zip_file.write(str(target / name), name)
    with zipfile.ZipFile(str(zip_path)) as zip_file:
        if len(zip_file.infolist()) != len(index) or set(zip_file.namelist()) != set(index) or zip_file.testzip():
            raise ValueError("ZIP membership or CRC differs")
        for name, expected in index.items():
            raw = zip_file.read(name)
            if len(raw) != expected["bytes"] or digest(raw) != expected["sha256"]:
                raise ValueError("ZIP artifact differs")
    return {"name": zip_path.name, "sha256": digest(zip_path.read_bytes()), "bytes": zip_path.stat().st_size,
            "members_verified": len(index)}

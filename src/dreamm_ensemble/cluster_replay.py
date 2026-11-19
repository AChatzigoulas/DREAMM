"""Preserve the legacy fit sequence and compare assignments by identity."""
from collections import defaultdict
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Association:
    frame: int
    pocket_rank: int
    residue: str
    center: tuple

    @property
    def key(self):
        return (self.frame, self.pocket_rank, self.residue)


def fit_legacy(rows, *, kmeans, meanshift, silhouette, matrix=tuple):
    """Replay the legacy fit order with injected pinned sklearn/pandas adapters.

    No random-state override. `matrix` must return the same float array/dataframe
    representation for both scalar and batched comparisons.
    """
    rows = tuple(rows)
    if not rows:
        raise ValueError("clustering requires a completed nonempty association table")
    if (len({row.key for row in rows}) != len(rows)
            or any(len(row.center) != 3 or not all(map(math.isfinite, row.center)) for row in rows)):
        raise ValueError("ambiguous or invalid clustering row")
    by_frame = defaultdict(set)
    for row in rows:
        by_frame[row.frame].add(row.pocket_rank)
    full = tuple(row.center for row in rows)
    unique = tuple(dict.fromkeys(full))
    start = max(map(len, by_frame.values()))
    fits = []
    if start > 1:
        if start > len(unique):
            raise ValueError("legacy fit has more pockets than unique centers")
        count, best = start, 0
        for k in range(start, min(3 * start, len(unique))):
            model = kmeans(n_clusters=k, n_init=100, max_iter=5000)
            labels = model.fit(matrix(unique)).labels_
            score = float(silhouette(matrix(unique), labels, metric="euclidean"))
            if not math.isfinite(score):
                raise ValueError("invalid silhouette score")
            fits.append(("scan", k, score))
            if score > best:
                count, best = k, score
        model = kmeans(n_clusters=count, n_init=100, max_iter=5000)
        algorithm = "KMeans"
    else:
        count = None
        model = meanshift(bandwidth=4, max_iter=1000, n_jobs=-1)
        algorithm = "MeanShift"
    model.fit_predict(matrix(unique))
    fits.append(("unique_fit", count, len(unique)))
    labels = tuple(int(x) for x in model.fit_predict(matrix(full)))
    fits.append(("full_fit", count, len(full)))
    return {"algorithm": algorithm, "fits": fits, "labels": labels, "partitions": partitions(rows, labels)}


def partitions(rows, labels):
    rows, labels = tuple(rows), tuple(labels)
    if len(rows) != len(labels) or len({row.key for row in rows}) != len(rows):
        raise ValueError("unproven clustering membership")
    groups = defaultdict(list)
    for row, label in zip(rows, labels):
        groups[label].append(row.key)
    return tuple(sorted(tuple(sorted(g)) for g in groups.values()))


def metric_values(source_rows, residue_ids):
    """Select by identity in source table order, as the legacy pandas mask did.

    Return the vector for the original mean or median reducer. Calculating a
    metric from a partially matched set is a distinct scientific decision.
    """
    rows = tuple(source_rows)
    wanted = set(residue_ids)
    identities = {key for key, _ in rows}
    if len(identities) != len(rows) or not wanted or not wanted <= identities:
        raise ValueError("unproven conservation or ESSA residue correspondence")
    return tuple(value for key, value in rows if key in wanted)

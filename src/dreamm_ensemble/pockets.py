"""Offline candidate for the legacy no-PBC distance kernel, with explicit identities."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Pocket:
    rank: int
    score: float
    center: tuple
    surface_ids: tuple


def _rows(coordinates, selected, pockets):
    """Validate references to one independently bound atom namespace."""
    if (any(type(key) is not int or key < 1 or len(xyz) != 3 or not all(map(math.isfinite, xyz))
            for key, xyz in coordinates.items()) or
            any(not uid or not ids or any(type(i) is not int or i not in coordinates for i in ids)
                for uid, ids in selected.items())):
        raise ValueError("unresolved frame residue or protein atom")
    ranks = set()
    for p in pockets:
        if (type(p.rank) is not int or p.rank < 1 or p.rank in ranks or not math.isfinite(p.score)
                or len(p.center) != 3 or not all(map(math.isfinite, p.center))
                or not p.surface_ids or len(p.surface_ids) > 99999
                or any(type(i) is not int or i not in coordinates for i in p.surface_ids)):
            raise ValueError("invalid pocket or unproven surface atom reference")
        ranks.add(p.rank)


def _minimum(kernel, left, right):
    # The adapter must supply the original serial distance_array implementation.
    # Shape, finiteness and nonnegative values are checked before publication.
    result = kernel(left, right)
    if len(result) != len(left):
        raise ValueError("distance row count differs")
    low = math.inf
    for row in result:
        if len(row) != len(right):
            raise ValueError("distance column count differs")
        for distance in row:
            if not math.isfinite(distance) or distance < 0:
                raise ValueError("invalid distance")
            low = min(low, float(distance))
    return low


def _association(uid, pocket, surface, center):
    return (uid, pocket.rank, pocket.score, surface, center)


def scalar_filter(coordinates, selected, pockets, kernel):
    """Baseline replay of the per-atom call shape, after identity validation."""
    _rows(coordinates, selected, pockets)
    results = []
    for uid, ids in selected.items():
        for pocket in pockets:
            surface = min(_minimum(kernel, (coordinates[a],), (coordinates[b],))
                          for a in ids for b in pocket.surface_ids)
            center = min(_minimum(kernel, (coordinates[a],), (pocket.center,)) for a in ids)
            if surface < 5:
                results.append(_association(uid, pocket, surface, center))
    return results


def batched_filter(coordinates, selected, pockets, kernel):
    """Resolve geometry once. Batch exactly the same pairs, with strict <5."""
    _rows(coordinates, selected, pockets)
    lookup = {p.rank: tuple(coordinates[b] for b in p.surface_ids) for p in pockets}
    results = []
    for uid, ids in selected.items():
        atoms = tuple(coordinates[a] for a in ids)
        for pocket in pockets:
            surface_atoms = lookup[pocket.rank]
            # Bound each matrix to 64 * 256 distance cells, including large residues.
            surface = min(_minimum(kernel, atoms[a:a+64], surface_atoms[b:b+256])
                          for a in range(0, len(atoms), 64)
                          for b in range(0, len(surface_atoms), 256))
            if surface < 5:
                center = min(_minimum(kernel, atoms[a:a+64], (pocket.center,))
                             for a in range(0, len(atoms), 64))
                results.append(_association(uid, pocket, surface, center))
    return results

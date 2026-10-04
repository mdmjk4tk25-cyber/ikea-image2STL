"""Minimal STL reader and watertightness check (stdlib only)."""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

Vertex = tuple[float, float, float]
Triangle = tuple[Vertex, Vertex, Vertex]


def read_stl(path: str | Path) -> list[Triangle]:
    data = Path(path).read_bytes()
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if 84 + count * 50 == len(data):
            tris = []
            for i in range(count):
                v = struct.unpack_from("<12f", data, 84 + i * 50)
                tris.append((v[3:6], v[6:9], v[9:12]))
            return tris
    return _read_ascii(data.decode("ascii", errors="replace"))


def _read_ascii(text: str) -> list[Triangle]:
    verts = [
        tuple(float(c) for c in line.split()[1:4])
        for line in text.splitlines()
        if line.strip().startswith("vertex")
    ]
    if not verts or len(verts) % 3:
        raise ValueError("not a valid STL file")
    return [tuple(verts[i : i + 3]) for i in range(0, len(verts), 3)]  # type: ignore[misc]


@dataclass
class MeshReport:
    triangles: int
    boundary_edges: int  # used by one triangle: holes
    nonmanifold_edges: int  # used by more than two triangles
    misoriented_edges: int  # same direction twice: flipped normals
    degenerate_triangles: int
    bbox_min: Vertex
    bbox_max: Vertex

    @property
    def watertight(self) -> bool:
        return (
            self.triangles > 0
            and self.boundary_edges == 0
            and self.nonmanifold_edges == 0
            and self.misoriented_edges == 0
        )


def check_mesh(triangles: list[Triangle]) -> MeshReport:
    """Every edge of a closed, consistently oriented mesh is shared by exactly
    two triangles that traverse it in opposite directions."""
    directed: Counter[tuple[Vertex, Vertex]] = Counter()
    degenerate = 0
    for a, b, c in triangles:
        if a == b or b == c or a == c:
            degenerate += 1
            continue
        for u, v in ((a, b), (b, c), (c, a)):
            directed[(u, v)] += 1

    undirected: Counter[frozenset] = Counter()
    misoriented = 0
    for (u, v), n in directed.items():
        undirected[frozenset((u, v))] += n
        if n > 1:
            misoriented += 1
    boundary = sum(1 for n in undirected.values() if n == 1)
    nonmanifold = sum(1 for n in undirected.values() if n > 2)

    pts = [p for t in triangles for p in t] or [(0.0, 0.0, 0.0)]
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    return MeshReport(len(triangles), boundary, nonmanifold, misoriented, degenerate, lo, hi)  # type: ignore[arg-type]

import struct

from furniture_cli.mesh import check_mesh, read_stl

A, B, C, D = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
TETRA = [(A, C, B), (A, B, D), (B, C, D), (C, A, D)]


def test_closed_tetrahedron_is_watertight():
    r = check_mesh(TETRA)
    assert r.watertight and r.bbox_min == A and r.bbox_max == (1.0, 1.0, 1.0)


def test_hole_is_detected():
    r = check_mesh(TETRA[:-1])
    assert not r.watertight and r.boundary_edges == 3


def test_flipped_triangle_is_detected():
    a, b, c = TETRA[0]
    r = check_mesh([(a, c, b), *TETRA[1:]])
    assert not r.watertight and r.misoriented_edges > 0


def test_binary_stl_roundtrip(tmp_path):
    path = tmp_path / "t.stl"
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(TETRA)))
        for tri in TETRA:
            f.write(struct.pack("<12fH", 0, 0, 0, *tri[0], *tri[1], *tri[2], 0))
    assert check_mesh(read_stl(path)).watertight


def test_ascii_stl(tmp_path):
    lines = ["solid t"]
    for tri in TETRA:
        lines += ["facet normal 0 0 0", "outer loop"]
        lines += [f"vertex {x} {y} {z}" for x, y, z in tri]
        lines += ["endloop", "endfacet"]
    lines.append("endsolid t")
    path = tmp_path / "t.stl"
    path.write_text("\n".join(lines))
    assert check_mesh(read_stl(path)).watertight

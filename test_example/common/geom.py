"""Geometry measurement helpers shared by CadQuery task harnesses.

These operate on executed geometry (cq.Shape built from a candidate script,
usually via harness_base.spawn_run + a .brep round-trip) instead of on the
candidate's source code, so checks survive renames and restructuring.

Boolean measurements use exact OCC b-rep operations; region_diff_volume falls
back to manifold3d mesh booleans when the b-rep boolean fails (OCC booleans
can be fragile on heavily filleted imports).
"""
from __future__ import annotations


def cylindrical_faces(shape, rmax=None, zband=None, vertical_only=True):
    """Cylindrical faces of `shape` as dicts with axis location and radius.

    A filtered view of brep_faces: each entry is {"r", "x", "y", "dz",
    "zmin", "zmax"} where (x, y) is the cylinder axis location and dz the
    |z| component of the axis direction. `zband=(lo, hi)` keeps only faces
    whose bbox overlaps the band.
    """
    out = []
    for entry in brep_faces(shape):
        if entry["kind"] != "cylinder":
            continue
        dz = abs(float(entry["axis_dir"][2]))
        if vertical_only and abs(dz - 1.0) > 1e-6:
            continue
        r = float(entry["radius"])
        if rmax is not None and r > rmax:
            continue
        (zmin, zmax) = (entry["bounds"][0][2], entry["bounds"][1][2])
        if zband is not None and (zmax < zband[0] or zmin > zband[1]):
            continue
        out.append({"r": r, "x": float(entry["axis_point"][0]),
                    "y": float(entry["axis_point"][1]), "dz": dz,
                    "zmin": float(zmin), "zmax": float(zmax)})
    return out


def region_diff_volume(a, b, clip):
    """Symmetric-difference volume of shapes `a` and `b` inside `clip`.

    ~0 means the two shapes are geometrically identical within the region,
    and that threshold is the whole point of the function.

    NO MESH FALLBACK. This used to answer a failed OCC boolean with the
    same question asked of tessellations instead, and return the number
    without saying which had produced it. The two do not answer alike: a
    mesh symmetric difference of two identical solids is the tessellation
    error, not ~0, so the fallback could not meet the contract above and
    the caller could not tell that it had been given a different kind of
    number. An exact boolean that fails is a fact about the geometry and
    it travels.
    """
    ca, cb = a.intersect(clip), b.intersect(clip)
    return ca.cut(cb).Volume() + cb.cut(ca).Volume()


def plane_basis(normal):
    """Deterministic orthonormal frame (e1, e2, n) for a section plane.

    The in-plane axes depend only on the normal, so 2D section coordinates
    are reproducible across calls and mappable back to 3D as
    p3 = e1*x + e2*y + n*offset.
    """
    import numpy as np
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    seed = np.array([0.0, 1.0, 0.0]) if abs(n[0]) > 0.5 else np.array([1.0, 0.0, 0.0])
    e1 = seed - n * (seed @ n)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(n, e1), n


def slice_polygons(mesh, normal, offset):
    """Cross-section of a trimesh at the plane {p . normal == offset}.

    Returns a list of shapely Polygons with holes assigned (trimesh's
    polygons_full resolves the containment hierarchy). 2D coordinates are
    in the plane_basis(normal) frame.
    """
    import numpy as np
    e1, e2, n = plane_basis(normal)
    section = mesh.section(plane_origin=n * float(offset), plane_normal=n)
    if section is None:
        return []

    T = np.eye(4)
    T[:3, :3] = np.vstack([e1, e2, n])
    T[:3, 3] = -T[:3, :3] @ (n * float(offset))
    #: A SECTION THAT WILL NOT RESOLVE IS NOT AN EMPTY SECTION. This used
    #: to return [] here, which every caller reads as "no material at this
    #: plane" -- the same answer `section is None` gives for a plane that
    #: genuinely misses the mesh. One is a fact about the part and the
    #: other is a fact about the slicer, and a wall measured as absent
    #: because the slicer failed is a wall reported as missing.
    to_2d = getattr(section, "to_2D", None) or section.to_planar
    planar, _ = to_2d(to_2D=T, check=False)
    polys = planar.polygons_full

    out = []
    for poly in polys:
        if poly is None:
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        if poly.geom_type == "Polygon":
            if poly.area > 1e-10:
                out.append(poly)
        else:
            out.extend(
                g for g in getattr(poly, "geoms", [])
                if getattr(g, "area", 0.0) > 1e-10
            )
    return out


def multiplane_areas(mesh, normal, offsets):
    """Cross-section areas (holes subtracted) at many parallel planes
    {p . normal == offset}, batched through trimesh's section_multiplane.

    Returns a float array aligned with `offsets`; 0.0 where a plane misses
    the mesh. A section that cannot be resolved raises rather than reading
    as an empty one -- see `slice_polygons`.
    """
    import numpy as np
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    offsets = np.asarray(offsets, float)
    try:
        sections = mesh.section_multiplane(np.zeros(3), n, offsets)
        return np.array(
            [0.0 if s is None else float(s.area) for s in sections],
            dtype=float,
        )
    except Exception:
        return np.array(
            [sum(p.area for p in slice_polygons(mesh, n, o)) for o in offsets],
            dtype=float,
        )


def max_inscribed_radius(poly):
    """Radius of the largest circle that fits inside a shapely Polygon
    (GEOS MaximumInscribedCircle; exact up to GEOS's automatic tolerance)."""
    import shapely
    if poly.is_empty:
        return 0.0
    return float(shapely.maximum_inscribed_circle(poly).length)


def plane_crossing_creases(mesh, normal, offset):
    """Concave creases of a trimesh that cross the plane {p . normal == offset}.

    Uses face_adjacency_angles: every interior mesh edge whose endpoints
    straddle the plane contributes its dihedral angle (degrees of deviation
    from coplanar between its two faces) at the point where it pierces the
    plane. Returns (angles_deg, points), where points are the 3D piercing
    points, restricted to concave (reflex) creases. Both arrays may be
    empty.
    """
    import numpy as np
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    adjacency = mesh.face_adjacency
    if len(adjacency) == 0:
        return np.empty(0), np.empty((0, 3))

    segments = mesh.vertices[mesh.face_adjacency_edges]
    qa = segments[:, 0] @ n - float(offset)
    qb = segments[:, 1] @ n - float(offset)
    crossing = (qa * qb <= 0.0) & (np.abs(qb - qa) > 1e-12)
    crossing &= ~mesh.face_adjacency_convex
    if not crossing.any():
        return np.empty(0), np.empty((0, 3))

    frac = qa[crossing] / (qa[crossing] - qb[crossing])
    points = (
        segments[crossing, 0]
        + frac[:, None] * (segments[crossing, 1] - segments[crossing, 0])
    )
    angles = np.degrees(mesh.face_adjacency_angles[crossing])
    return angles, points


def to_manifold(mesh):
    """trimesh.Trimesh -> manifold3d.Manifold, or None if the mesh is not a
    valid closed manifold.

    Manifold booleans are robust to the tangent / coincident-face contact
    that makes OCC booleans throw; volumes come back via .volume(), rigid
    transforms via Manifold.transform(m[:3, :4]).
    """
    import numpy as np
    import manifold3d
    try:
        solid = manifold3d.Manifold(
            mesh=manifold3d.Mesh(
                vert_properties=np.asarray(mesh.vertices, np.float32),
                tri_verts=np.asarray(mesh.faces, np.uint32),
            )
        )
        if solid.status() != manifold3d.Error.NoError:
            return None
        return solid
    except Exception:
        return None


def brep_faces(shape):
    """Classify every face of a cq.Shape by its exact analytic surface.

    Returns a list of dicts with 'kind' in {'cylinder', 'cone', 'plane',
    'torus', 'other'}, the surface parameters (radius/axis for cylinders,
    ref_radius/semi_angle/axis for cones, origin/normal for planes,
    major_radius/minor_radius/axis for tori), the
    face's world 'bounds' as a (2, 3) array, and its 'area'. Lets harnesses
    measure features (bores, pins, walls) exactly instead of via mesh
    slicing. See cylindrical_faces for a filtered cylinders-only view.
    """
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Plane, GeomAbs_Torus,
    )

    def _xyz(v):
        return np.array([v.X(), v.Y(), v.Z()], dtype=float)

    out = []
    for face in shape.Faces():
        try:
            adaptor = BRepAdaptor_Surface(face.wrapped)
            surface_type = adaptor.GetType()
            bb = face.BoundingBox()
            entry = {
                "face": face,
                "area": float(face.Area()),
                "bounds": np.array(
                    [[bb.xmin, bb.ymin, bb.zmin], [bb.xmax, bb.ymax, bb.zmax]],
                    dtype=float,
                ),
            }
            if surface_type == GeomAbs_Cylinder:
                cylinder = adaptor.Cylinder()
                axis = cylinder.Axis()
                entry.update(
                    kind="cylinder",
                    radius=float(cylinder.Radius()),
                    axis_point=_xyz(axis.Location()),
                    axis_dir=_xyz(axis.Direction()),
                )
            elif surface_type == GeomAbs_Cone:
                cone = adaptor.Cone()
                axis = cone.Axis()
                entry.update(
                    kind="cone",
                    ref_radius=float(cone.RefRadius()),
                    semi_angle=float(cone.SemiAngle()),
                    axis_point=_xyz(axis.Location()),
                    axis_dir=_xyz(axis.Direction()),
                )
            elif surface_type == GeomAbs_Plane:
                plane = adaptor.Plane()
                axis = plane.Axis()
                entry.update(
                    kind="plane",
                    origin=_xyz(axis.Location()),
                    normal=_xyz(axis.Direction()),
                )
            elif surface_type == GeomAbs_Torus:
                torus = adaptor.Torus()
                axis = torus.Axis()
                entry.update(
                    kind="torus",
                    major_radius=float(torus.MajorRadius()),
                    minor_radius=float(torus.MinorRadius()),
                    axis_point=_xyz(axis.Location()),
                    axis_dir=_xyz(axis.Direction()),
                )
            else:
                entry.update(kind="other")
            out.append(entry)
        except Exception:
            continue
    return out


def cone_radius_at(entry, z):
    """Radius of a brep_faces 'cone' entry at world height z, assuming the
    cone axis is parallel to Z.

    RefRadius is the radius at the axis Location; it grows by
    tan(semi_angle) per unit along the axis direction.
    """
    import numpy as np
    dz = (float(z) - float(entry["axis_point"][2])) * float(entry["axis_dir"][2])
    return abs(float(entry["ref_radius"]) + dz * np.tan(float(entry["semi_angle"])))


def to_trimesh(shape, tolerance=0.005):
    """Tessellate a cq.Shape into a trimesh.Trimesh. `tolerance` is the
    linear deflection in mm; keep it well below the smallest scored
    tolerance of the calling harness."""
    import numpy as np
    import trimesh
    vertices, faces = shape.tessellate(tolerance)
    return trimesh.Trimesh(
        vertices=np.array([[p.x, p.y, p.z] for p in vertices], dtype=float),
        faces=np.array(faces, dtype=int),
        process=True,
    )


# ---------------------------------------------------------------------------
# Sampled point-cloud comparison between two meshes in a SHARED frame.
# No alignment is performed: callers that care about pose compare raw
# coordinates and score misplacement as error. Self-contained: only
# numpy/trimesh/point_cloud_utils, imported lazily.

def surface_points(mesh, n, seed=0):
    """n surface-sampled points of a trimesh, seeded for determinism."""
    import numpy as np
    import trimesh
    pts, _ = trimesh.sample.sample_surface(mesh, n, seed=seed)
    return np.asarray(pts, dtype=float)


def _pcu():
    """point_cloud_utils, or None where it cannot be installed.

    env_requirements.txt pins point-cloud-utils==0.34.0, which ships no
    wheel for Python 3.14 -- only a C++ sdist. On such a host every metric
    below raised ModuleNotFoundError, and harnesses that wrap geometry in
    `except Exception` scored even the REFERENCE 0 on all of it
    (5_robot_scan: 1/9 for its own solution). The scipy fallback computes
    the same Euclidean nearest-neighbour distances.
    """
    try:
        import point_cloud_utils as pcu
        return pcu
    except ImportError:
        return None


def _nn_dists(a, b):
    """Nearest-neighbor distance from each point of `a` to cloud `b`."""
    import numpy as np
    a = np.ascontiguousarray(a, dtype=float)
    b = np.ascontiguousarray(b, dtype=float)
    pcu = _pcu()
    if pcu is not None:
        d, _ = pcu.k_nearest_neighbors(a, b, 1)
        return np.asarray(d, dtype=float).ravel()
    from scipy.spatial import cKDTree
    d, _ = cKDTree(b).query(a, k=1)
    return np.asarray(d, dtype=float).ravel()


def f_score(a_pts, b_pts, tau):
    """F1 of precision/recall at distance threshold tau (a=candidate,
    b=ground truth)."""
    precision = float((_nn_dists(a_pts, b_pts) < tau).mean())
    recall = float((_nn_dists(b_pts, a_pts) < tau).mean())
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)

#!/usr/bin/env python3
"""
=============================================================================
Pepakura Alternative: Universal 3D Papercraft & Plotter Pipeline
=============================================================================
A fully autonomous, precision papercraft template generator for manual crafting
and cutting plotters (Cricut, Silhouette, Brother, etc.).

Accepts ANY 3D model (.obj) and produces production-grade vector templates:
  - Intelligent Unit & Scale Detection: Automatically detects meters, cm, or mm
  - Page Format: US Letter (8.5 x 11.0 in / 215.9 x 279.4 mm) or A4
  - Margins: Strict >= 15.0 mm clearance from all page edges
  - Part Spacing: Strict >= 5.0 mm clearance between adjacent parts
  - Outward Glue Tabs: 45° base angle with faint grey fill (#E6E6E6), configurable
    depth in mm, and fine diagonal cross-hatch pattern (#B8B8B8)
  - Edge Matching IDs: Protected badges on tabs, exterior placement on cut edges
  - Fold Classifications:
      * Mountain Folds: Dashed Red (#D32F2F)
      * Valley Folds: Dash-Dot Blue (#1976D2)
      * Cut Outlines: Continuous Solid Black (#000000)
  - Output Variations:
      1) Full Color + Fold Lines + Edge IDs
      2) Full Color (No Lines, clean cut outlines only)
      3) Blank White + Fold Lines + Edge IDs
      4) Blank White (No Lines, clean cut outlines only)
  - Export Formats: Vector PDF, Multi-layer SVG, and 300 DPI PNG (2550x3300 px)
=============================================================================
"""

import os
import sys
import copy
import math
import base64
import argparse
from io import BytesIO
from PIL import Image
import numpy as np

from reportlab.pdfgen import canvas
from reportlab.lib import colors
import fitz

MM_TO_PT = 72.0 / 25.4  # 1 mm in PostScript points = 2.834645669 pt


# =============================================================================
# 1. 3D OBJ PARSER & AUTO-SCALE DETECTION
# =============================================================================

class Model3D:
    """Universal 3D Mesh container supporting any polygon model."""
    def __init__(self):
        self.vertices = []       # [(x, y, z), ...] in model units
        self.uvs = []            # [(u, v), ...]
        self.normals = []        # [(nx, ny, nz), ...]
        self.faces = []          # [ {'v': [v0, v1, ..], 'vt': [t0, t1, ..]}, ...]
        self.material_file = None
        self.texture_file = None

    @classmethod
    def load_obj(cls, filepath):
        """Loads any Wavefront OBJ file."""
        model = cls()
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"OBJ file not found: {filepath}")

        base_dir = os.path.dirname(filepath)
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                tokens = line.split()
                tag = tokens[0].lower()

                if tag == 'v' and len(tokens) >= 4:
                    model.vertices.append((float(tokens[1]), float(tokens[2]), float(tokens[3])))
                elif tag == 'vt' and len(tokens) >= 3:
                    model.uvs.append((float(tokens[1]), float(tokens[2])))
                elif tag == 'vn' and len(tokens) >= 4:
                    model.normals.append((float(tokens[1]), float(tokens[2]), float(tokens[3])))
                elif tag == 'mtllib' and len(tokens) >= 2:
                    mtl_candidate = os.path.join(base_dir, tokens[1])
                    if os.path.exists(mtl_candidate):
                        model.material_file = mtl_candidate
                        model._parse_mtl(mtl_candidate, base_dir)
                elif tag == 'f' and len(tokens) >= 4:
                    f_verts = []
                    f_uvs = []
                    for vert_spec in tokens[1:]:
                        parts = vert_spec.split('/')
                        v_idx = int(parts[0]) - 1
                        f_verts.append(v_idx)
                        if len(parts) >= 2 and parts[1]:
                            f_uvs.append(int(parts[1]) - 1)
                    model.faces.append({'v': f_verts, 'vt': f_uvs})

        print(f"Loaded 3D Mesh: {len(model.vertices)} vertices, {len(model.faces)} faces, {len(model.uvs)} UVs.")
        return model

    def _parse_mtl(self, mtl_path, base_dir):
        """Extracts texture map image from associated MTL file if available."""
        try:
            with open(mtl_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line.lower().startswith('map_kd') and len(line.split()) >= 2:
                        tex_name = line.split(None, 1)[1]
                        candidate = os.path.join(base_dir, tex_name)
                        if os.path.exists(candidate):
                            self.texture_file = candidate
                            break
        except Exception as e:
            print(f"MTL notice: {e}")

    @classmethod
    def create_default_model(cls):
        """Generates a default low-poly geometric 3D model for demonstration (in mm)."""
        model = cls()
        angles = [i * (2.0 * math.pi / 6.0) for i in range(6)]
        r_bottom, r_top = 35.0, 25.0
        h = 50.0

        for a in angles:
            model.vertices.append((r_bottom * math.cos(a), r_bottom * math.sin(a), 0.0))
        for a in angles:
            model.vertices.append((r_top * math.cos(a), r_top * math.sin(a), h))
        model.vertices.append((0.0, 0.0, h + 22.0))

        # Bottom face
        model.faces.append({'v': [5, 4, 3, 2, 1, 0], 'vt': []})
        # 6 Side wall quads
        for i in range(6):
            ni = (i + 1) % 6
            model.faces.append({'v': [i, ni, ni + 6, i + 6], 'vt': []})
        # 6 Roof triangles meeting at peak (12)
        for i in range(6):
            ni = (i + 1) % 6
            model.faces.append({'v': [i + 6, ni + 6, 12], 'vt': []})

        return model


def compute_auto_scale(vertices, user_scale=None):
    """
    Intelligent unit detection:
      - If max dimension < 2.0 (e.g. 0.08 to 0.5), model is in METERS -> scale by 1000.0 (mm)
      - If max dimension < 25.0 (e.g. 5 to 15), model is in CENTIMETERS -> scale by 10.0 (mm)
      - If max dimension > 300.0 (e.g. huge architectural units) -> scale to fit printable sheet
      - Otherwise, model is already in millimeters -> scale by 1.0
    """
    if user_scale is not None and user_scale > 0:
        return user_scale

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    max_dim = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))

    if max_dim < 2.0:
        print(f"Auto-detected model unit: METERS (max dimension: {max_dim:.4f} m). Scaling by 1000.0 to millimeters.")
        return 1000.0
    elif max_dim < 25.0:
        print(f"Auto-detected model unit: CENTIMETERS (max dimension: {max_dim:.2f} cm). Scaling by 10.0 to millimeters.")
        return 10.0
    elif max_dim > 300.0:
        target_s = 150.0 / max_dim
        print(f"Model dimensions ({max_dim:.1f}) exceed printable sheet size. Auto-scaling by {target_s:.4f}.")
        return target_s
    else:
        print(f"Auto-detected model unit: MILLIMETERS (max dimension: {max_dim:.1f} mm). Scale = 1.0.")
        return 1.0


# =============================================================================
# 2. 2D GEOMETRY & FLAP GENERATOR
# =============================================================================

class PaperPart:
    """Represents a flat 2D unfolded papercraft island/part."""
    def __init__(self, part_id, name="Part"):
        self.part_id = part_id
        self.name = name
        self.poly2d = []             # Perimeter points [(x, y), ...] in mm
        self.edge_3d_keys = []       # Associated 3D edge keys for perimeter edges
        self.edge_3d_mids = []       # Associated 3D edge midpoints
        self.internal_folds = []     # [ ((p1, p2), 'mountain' | 'valley'), ... ]
        self.tabs = []               # [ {'edge': (p1, p2), 'poly': [...], 'id': int}, ... ]
        self.edge_labels = []        # [ {'pos': (x,y), 'id': int}, ... ]
        self.texture_crop = None     # PIL Image or None
        self.sub_faces = []          # Multi-face island sub-polygons [{'fi', 'poly2d', ...}]
        self._tex_img = None         # Reference to source texture for non-90° re-baking

    def compute_poly_bounds(self):
        if not self.poly2d:
            return 0, 0, 0, 0
        min_x = min(p[0] for p in self.poly2d)
        max_x = max(p[0] for p in self.poly2d)
        min_y = min(p[1] for p in self.poly2d)
        max_y = max(p[1] for p in self.poly2d)
        return min_x, min_y, max_x, max_y

    def compute_bounds(self):
        pts = list(self.poly2d)
        for t in self.tabs:
            pts.extend(t['poly'])
        if not pts:
            return 0, 0, 0, 0
        min_x = min(p[0] for p in pts)
        max_x = max(p[0] for p in pts)
        min_y = min(p[1] for p in pts)
        max_y = max(p[1] for p in pts)
        return min_x, min_y, max_x, max_y

    def translate(self, dx, dy):
        self.poly2d = [(p[0] + dx, p[1] + dy) for p in self.poly2d]
        for sf in self.sub_faces:
            sf['poly2d'] = [(p[0] + dx, p[1] + dy) for p in sf['poly2d']]
        self.internal_folds = [
            (((p1[0] + dx, p1[1] + dy), (p2[0] + dx, p2[1] + dy)), ftype)
            for (p1, p2), ftype in self.internal_folds
        ]
        for t in self.tabs:
            t['edge'] = ((t['edge'][0][0] + dx, t['edge'][0][1] + dy),
                         (t['edge'][1][0] + dx, t['edge'][1][1] + dy))
            t['poly'] = [(p[0] + dx, p[1] + dy) for p in t['poly']]
        for l in self.edge_labels:
            l['pos'] = (l['pos'][0] + dx, l['pos'][1] + dy)

    def snapshot_geometry(self):
        return copy.deepcopy((
            self.poly2d, self.sub_faces, self.internal_folds, self.tabs, self.edge_labels
        ))

    def restore_geometry(self, snap):
        p, sf, inf, tb, el = copy.deepcopy(snap)
        self.poly2d = p
        self.sub_faces = sf
        self.internal_folds = inf
        self.tabs = tb
        self.edge_labels = el


def compute_outward_normal(poly, p1, p2):
    """Computes unit 2D normal vector pointing strictly outward from the polygon."""
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    mx = (p1[0] + p2[0]) * 0.5
    my = (p1[1] + p2[1]) * 0.5
    vx = mx - cx
    vy = my - cy

    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return (0.0, 1.0)
    ux = dx / L
    uy = dy / L

    n1 = (-uy, ux)
    n2 = (uy, -ux)
    return n1 if (n1[0] * vx + n1[1] * vy) > (n2[0] * vx + n2[1] * vy) else n2


def generate_outward_flap(poly, p1, p2, depth=5.5, taper_angle=45.0):
    """Generates an outward trapezoidal glue tab with 45° base angle and exact depth in mm."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    L = math.hypot(dx, dy)
    if L < 1e-4:
        return [p1, p2, p2, p1]

    ux = dx / L
    uy = dy / L
    nx, ny = compute_outward_normal(poly, p1, p2)

    rad = math.radians(taper_angle)
    offset = depth / math.tan(rad)
    max_offset = L * 0.38
    if offset > max_offset:
        offset = max_offset

    t1 = (p1[0] + offset * ux + depth * nx, p1[1] + offset * uy + depth * ny)
    t2 = (p2[0] - offset * ux + depth * nx, p2[1] - offset * uy + depth * ny)
    return [p1, t1, t2, p2]


# =============================================================================
# 3. 3D SEAM MATCHING & TOPOLOGY SOLVER
# =============================================================================

def quantize_pt(pt, precision=2):
    """Quantizes 3D spatial coordinate for robust mesh boundary matching."""
    return (round(pt[0], precision), round(pt[1], precision), round(pt[2], precision))


def make_3d_edge_key(p1, p2):
    """Creates an unordered 3D edge key from two endpoint positions."""
    q1 = quantize_pt(p1)
    q2 = quantize_pt(p2)
    return tuple(sorted([q1, q2]))


def project_face_to_2d(verts_3d):
    """Projects a 3D planar polygon into 2D preserving exact 3D edge lengths in mm."""
    v0 = verts_3d[0]
    # Edge 0 as u-axis
    e0 = (verts_3d[1][0] - v0[0], verts_3d[1][1] - v0[1], verts_3d[1][2] - v0[2])
    e0_len = math.hypot(e0[0], math.hypot(e0[1], e0[2]))
    if e0_len < 1e-6:
        u_axis = (1.0, 0.0, 0.0)
    else:
        u_axis = (e0[0] / e0_len, e0[1] / e0_len, e0[2] / e0_len)

    # Calculate face normal
    ax, ay, az = e0
    bx = verts_3d[2][0] - v0[0]
    by = verts_3d[2][1] - v0[1]
    bz = verts_3d[2][2] - v0[2]
    nx = ay * bz - az * by
    ny = az * bx - ax * bz
    nz = ax * by - ay * bx
    nl = math.hypot(nx, math.hypot(ny, nz))
    if nl > 1e-6:
        norm = (nx / nl, ny / nl, nz / nl)
    else:
        norm = (0.0, 0.0, 1.0)

    # v-axis = normal x u-axis
    v_axis = (
        norm[1] * u_axis[2] - norm[2] * u_axis[1],
        norm[2] * u_axis[0] - norm[0] * u_axis[2],
        norm[0] * u_axis[1] - norm[1] * u_axis[0]
    )

    poly2d = []
    for v in verts_3d:
        rel = (v[0] - v0[0], v[1] - v0[1], v[2] - v0[2])
        pu = rel[0] * u_axis[0] + rel[1] * u_axis[1] + rel[2] * u_axis[2]
        pv = rel[0] * v_axis[0] + rel[1] * v_axis[1] + rel[2] * v_axis[2]
        poly2d.append((pu, pv))

    return poly2d, norm


# =============================================================================
# 2B. POLYGON CLEARANCE, FLAP COLLISION, AFFINE TEXTURE & ISLAND JOINING
# =============================================================================

def _dist3d(a, b):
    return math.hypot(a[0] - b[0], math.hypot(a[1] - b[1], a[2] - b[2]))


def _cross2(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segs_strict_intersect(a1, a2, b1, b2, eps=1e-4):
    d1 = _cross2(b1, b2, a1)
    d2 = _cross2(b1, b2, a2)
    d3 = _cross2(a1, a2, b1)
    d4 = _cross2(a1, a2, b2)
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
           ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps))


def _point_in_poly_strict(pt, poly, boundary_tol=0.05):
    x, y = pt
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        if l2 > 1e-12:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / l2))
            if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) < boundary_tol:
                return False
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1):
            inside = not inside
    return inside


def polys_strictly_overlap(p1, p2, tol=0.05):
    """Returns True if two 2D polygons overlap strictly inside each other's interiors."""
    if (max(p[0] for p in p1) <= min(p[0] for p in p2) + tol or
            max(p[0] for p in p2) <= min(p[0] for p in p1) + tol or
            max(p[1] for p in p1) <= min(p[1] for p in p2) + tol or
            max(p[1] for p in p2) <= min(p[1] for p in p1) + tol):
        return False
    n1, n2 = len(p1), len(p2)
    for i in range(n1):
        for j in range(n2):
            if _segs_strict_intersect(p1[i], p1[(i + 1) % n1], p2[j], p2[(j + 1) % n2]):
                return True
    pts1 = list(p1) + [
        (p1[i][0] * (1 - t) + p1[(i + 1) % n1][0] * t, p1[i][1] * (1 - t) + p1[(i + 1) % n1][1] * t)
        for i in range(n1) for t in (0.25, 0.5, 0.75)
    ] + [(sum(p[0] for p in p1) / n1, sum(p[1] for p in p1) / n1)]
    pts2 = list(p2) + [
        (p2[i][0] * (1 - t) + p2[(i + 1) % n2][0] * t, p2[i][1] * (1 - t) + p2[(i + 1) % n2][1] * t)
        for i in range(n2) for t in (0.25, 0.5, 0.75)
    ] + [(sum(p[0] for p in p2) / n2, sum(p[1] for p in p2) / n2)]
    for pt in pts1:
        if _point_in_poly_strict(pt, p2, boundary_tol=tol):
            return True
    for pt in pts2:
        if _point_in_poly_strict(pt, p1, boundary_tol=tol):
            return True
    return False


def _seg_to_seg_dist(a1, a2, b1, b2):
    if _segs_strict_intersect(a1, a2, b1, b2, eps=1e-6):
        return 0.0
    def pt_seg_dist(p, s1, s2):
        dx, dy = s2[0] - s1[0], s2[1] - s1[1]
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            return math.hypot(p[0] - s1[0], p[1] - s1[1])
        t = max(0.0, min(1.0, ((p[0] - s1[0]) * dx + (p[1] - s1[1]) * dy) / l2))
        return math.hypot(p[0] - (s1[0] + t * dx), p[1] - (s1[1] + t * dy))
    return min(pt_seg_dist(a1, b1, b2), pt_seg_dist(a2, b1, b2),
               pt_seg_dist(b1, a1, a2), pt_seg_dist(b2, a1, a2))


def poly_min_dist(p1, p2):
    """Exact minimum Euclidean distance between two closed 2D polygons (0.0 if overlapping)."""
    if polys_strictly_overlap(p1, p2, tol=0.01):
        return 0.0
    md = float('inf')
    n1, n2 = len(p1), len(p2)
    for i in range(n1):
        a1, a2 = p1[i], p1[(i + 1) % n1]
        for j in range(n2):
            d = _seg_to_seg_dist(a1, a2, p2[j], p2[(j + 1) % n2])
            if d < md:
                md = d
                if md == 0.0:
                    return 0.0
    return md


def _flaps_share_tight_corner(fp_a, e_a, fp_b, e_b, min_angle_deg=92.0):
    """Checks if two flaps meet at a sharp convex corner (< 92°) where 45° bevels collide."""
    a1, a2 = e_a
    b1, b2 = e_b
    pairs = [(a1, a2, b1, b2), (a1, a2, b2, b1), (a2, a1, b1, b2), (a2, a1, b2, b1)]
    for sa1, sa2, sb1, sb2 in pairs:
        if math.hypot(sa1[0] - sb1[0], sa1[1] - sb1[1]) < 0.1:
            va = (sa2[0] - sa1[0], sa2[1] - sa1[1])
            vb = (sb2[0] - sb1[0], sb2[1] - sb1[1])
            la, lb = math.hypot(*va), math.hypot(*vb)
            if la > 1e-4 and lb > 1e-4:
                cos_t = max(-1.0, min(1.0, (va[0] * vb[0] + va[1] * vb[1]) / (la * lb)))
                ang = math.degrees(math.acos(cos_t))
                if ang < min_angle_deg:
                    return True
    return False


def _miter_flap_against_tabs(fp, edge_pair, existing_tabs):
    """If an adjacent flap meets at a sharp corner (< 92°), trims the shared corner bevel to bisect."""
    p1, p2 = edge_pair
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if L < 1e-4:
        return fp
    ux, uy = (p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L
    # Deduce outward normal from fp[1] - fp[0]
    tx, ty = fp[1][0] - p1[0], fp[1][1] - p1[1]
    proj_u = tx * ux + ty * uy
    nx_raw, ny_raw = tx - proj_u * ux, ty - proj_u * uy
    depth = math.hypot(nx_raw, ny_raw)
    if depth < 1e-4:
        return fp
    nx, ny = nx_raw / depth, ny_raw / depth

    off1 = math.hypot(fp[1][0] - p1[0] - depth * nx, fp[1][1] - p1[1] - depth * ny)
    off2 = math.hypot(fp[2][0] - p2[0] - depth * nx, fp[2][1] - p2[1] - depth * ny)

    for et in existing_tabs:
        b1, b2 = et['edge']
        for corner, other_b in ((p1, p2), (p2, p1)):
            for eb_c, eb_o in ((b1, b2), (b2, b1)):
                if math.hypot(corner[0] - eb_c[0], corner[1] - eb_c[1]) < 0.1:
                    va = (other_b[0] - corner[0], other_b[1] - corner[1])
                    vb = (eb_o[0] - eb_c[0], eb_o[1] - eb_c[1])
                    la, lb = math.hypot(*va), math.hypot(*vb)
                    if la > 1e-4 and lb > 1e-4:
                        cos_t = max(-1.0, min(1.0, (va[0] * vb[0] + va[1] * vb[1]) / (la * lb)))
                        ang = math.acos(cos_t)
                        if ang < math.radians(92.0) and ang > 0.1:
                            req_off = depth / math.tan(ang * 0.5) + 0.15
                            if corner == p1:
                                off1 = max(off1, min(L * 0.44, req_off))
                            else:
                                off2 = max(off2, min(L * 0.44, req_off))
    t1 = (p1[0] + off1 * ux + depth * nx, p1[1] + off1 * uy + depth * ny)
    t2 = (p2[0] - off2 * ux + depth * nx, p2[1] - off2 * uy + depth * ny)
    return [p1, t1, t2, p2]


def bake_island_texture(sub_faces, tex_img, dpi=300):
    """Per-face affine texture rasterization at 300 DPI across arbitrarily joined/rotated islands."""
    all_pts = [pt for sf in sub_faces for pt in sf['poly2d']]
    if not all_pts or tex_img is None:
        return None
    min_x, max_x = min(p[0] for p in all_pts), max(p[0] for p in all_pts)
    min_y, max_y = min(p[1] for p in all_pts), max(p[1] for p in all_pts)
    w_mm = max(0.5, max_x - min_x)
    h_mm = max(0.5, max_y - min_y)

    px_per_mm = dpi / 25.4
    W_out = max(4, min(4000, int(math.ceil(w_mm * px_per_mm))))
    H_out = max(4, min(4000, int(math.ceil(h_mm * px_per_mm))))

    tex_arr = np.asarray(tex_img.convert('RGB'), dtype=np.uint8)
    H_tex, W_tex, _ = tex_arr.shape

    out_arr = np.empty((H_out, W_out, 3), dtype=np.uint8)
    out_arr[:, :, 0] = 251
    out_arr[:, :, 1] = 249
    out_arr[:, :, 2] = 245

    sx_scale = W_out / w_mm
    sy_scale = H_out / h_mm

    for sf in sub_faces:
        poly = sf['poly2d']
        uvs = sf.get('uvs', [])
        if not uvs or len(uvs) < len(poly):
            continue
        for k in range(1, len(poly) - 1):
            x0, y0 = (poly[0][0] - min_x) * sx_scale, (poly[0][1] - min_y) * sy_scale
            x1, y1 = (poly[k][0] - min_x) * sx_scale, (poly[k][1] - min_y) * sy_scale
            x2, y2 = (poly[k + 1][0] - min_x) * sx_scale, (poly[k + 1][1] - min_y) * sy_scale

            u0, v0 = uvs[0][0] * (W_tex - 1), (1.0 - uvs[0][1]) * (H_tex - 1)
            u1, v1 = uvs[k][0] * (W_tex - 1), (1.0 - uvs[k][1]) * (H_tex - 1)
            u2, v2 = uvs[k + 1][0] * (W_tex - 1), (1.0 - uvs[k + 1][1]) * (H_tex - 1)

            denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(denom) < 1e-8:
                continue

            bx0 = max(0, int(math.floor(min(x0, x1, x2))) - 1)
            bx1 = min(W_out, int(math.ceil(max(x0, x1, x2))) + 2)
            by0 = max(0, int(math.floor(min(y0, y1, y2))) - 1)
            by1 = min(H_out, int(math.ceil(max(y0, y1, y2))) + 2)
            if bx1 <= bx0 or by1 <= by0:
                continue

            gx = (np.arange(bx0, bx1, dtype=np.float32) + 0.5)[None, :]
            gy = (np.arange(by0, by1, dtype=np.float32) + 0.5)[:, None]

            w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / denom
            w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / denom
            w2 = 1.0 - w0 - w1

            mask = (w0 >= -0.03) & (w1 >= -0.03) & (w2 >= -0.03)
            if not np.any(mask):
                continue

            su = np.clip(w0[mask] * u0 + w1[mask] * u1 + w2[mask] * u2, 0.0, W_tex - 1.0)
            sv = np.clip(w0[mask] * v0 + w1[mask] * v1 + w2[mask] * v2, 0.0, H_tex - 1.0)

            iu0 = np.floor(su).astype(np.int32)
            iv0 = np.floor(sv).astype(np.int32)
            iu1 = np.minimum(iu0 + 1, W_tex - 1)
            iv1 = np.minimum(iv0 + 1, H_tex - 1)
            fu = (su - iu0)[:, None]
            fv = (sv - iv0)[:, None]

            c00 = tex_arr[iv0, iu0].astype(np.float32)
            c10 = tex_arr[iv0, iu1].astype(np.float32)
            c01 = tex_arr[iv1, iu0].astype(np.float32)
            c11 = tex_arr[iv1, iu1].astype(np.float32)
            interp = (c00 * (1 - fu) * (1 - fv) + c10 * fu * (1 - fv) +
                      c01 * (1 - fu) * fv + c11 * fu * fv).astype(np.uint8)
            out_arr[by0:by1, bx0:bx1][mask] = interp

    return Image.fromarray(out_arr, mode='RGB')


def _compute_hinge_transform(poly_a, a1, a2, p3_1, poly_b, verts_3d_b, edge_idx_b):
    """Rigid 2D transform placing poly_b strictly outward from poly_a across hinge (a1, a2)."""
    nb = len(verts_3d_b)
    if _dist3d(p3_1, verts_3d_b[edge_idx_b]) < 1e-1:
        b1, b2 = poly_b[edge_idx_b], poly_b[(edge_idx_b + 1) % nb]
    else:
        b1, b2 = poly_b[(edge_idx_b + 1) % nb], poly_b[edge_idx_b]

    nx, ny = compute_outward_normal(poly_a, a1, a2)
    da = (a2[0] - a1[0], a2[1] - a1[1])
    la = max(1e-6, math.hypot(*da))
    ux_a, uy_a = da[0] / la, da[1] / la

    db = (b2[0] - b1[0], b2[1] - b1[1])
    lb = max(1e-6, math.hypot(*db))
    ux_b, uy_b = db[0] / lb, db[1] / lb
    onx_b, ony_b = compute_outward_normal(poly_b, b1, b2)
    inx_b, iny_b = -onx_b, -ony_b

    def xform(p):
        dx, dy = p[0] - b1[0], p[1] - b1[1]
        u = dx * ux_b + dy * uy_b
        v = dx * inx_b + dy * iny_b
        return (a1[0] + u * ux_a + v * nx, a1[1] + u * uy_a + v * ny)
    return xform


def _trace_island_boundary(sub_faces, internal_keys):
    """Traces the single ordered outer boundary loop of a tree-of-faces island."""
    b_edges = []
    for sf in sub_faces:
        poly = sf['poly2d']
        eks = sf['edge_keys']
        emids = sf['edge_mids']
        n = len(poly)
        for ei in range(n):
            ek = eks[ei]
            if ek not in internal_keys:
                b_edges.append({
                    'p1': poly[ei],
                    'p2': poly[(ei + 1) % n],
                    'ek': ek,
                    'emid': emids[ei]
                })
    if not b_edges:
        return [], [], []

    used = [False] * len(b_edges)
    loop = [b_edges[0]]
    used[0] = True
    while len(loop) < len(b_edges):
        prev = loop[-1]
        px, py = prev['p2']
        in_ang = math.atan2(prev['p2'][1] - prev['p1'][1], prev['p2'][0] - prev['p1'][0])
        best_idx = None
        best_turn = -1e9
        for idx, cand in enumerate(b_edges):
            if used[idx]:
                continue
            if math.hypot(cand['p1'][0] - px, cand['p1'][1] - py) < 0.05:
                out_ang = math.atan2(cand['p2'][1] - cand['p1'][1], cand['p2'][0] - cand['p1'][0])
                diff = (out_ang - (in_ang + math.pi)) % (2.0 * math.pi)
                if diff > best_turn:
                    best_turn = diff
                    best_idx = idx
        if best_idx is None:
            break
        loop.append(b_edges[best_idx])
        used[best_idx] = True

    return [e['p1'] for e in loop], [e['ek'] for e in loop], [e['emid'] for e in loop]


def _unfold_model_joined(model, verts_mm, tex_img, flap_size_mm=5.5,
                          page_w=215.9, page_h=279.4, margin=15.0):
    """
    Intelligent Flap-Aware Edge-Joining Solver:
      1. Normalizes 3D face winding consistently outward per connected component.
      2. Classifies exact 3D dihedral angles into mountain/valley folds.
      3. Joins faces along shared edges (rings/strips first, then caps/bases) ONLY if:
         - Zero face-to-face overlap in 2D
         - Zero flap/circular gap collision (validates flaps on closure/external seams)
         - Merged island fits within printable page bounds (margin >= 15 mm)
      4. Assigns non-overlapping glue flaps (preferring clean corners, mitering if < 92°)
         and bakes 300 DPI affine UV textures across every sub-face.
    """
    cx_m = sum(v[0] for v in verts_mm) / len(verts_mm)
    cy_m = sum(v[1] for v in verts_mm) / len(verts_mm)
    cz_m = sum(v[2] for v in verts_mm) / len(verts_mm)

    raw_faces = [
        {'v_idx': list(f['v']), 'uv_idx': list(f['vt']) if f.get('vt') else []}
        for f in model.faces
    ]

    # Build initial edge adjacency and normalize outward winding per connected component
    em_init = {}
    for fi, rf in enumerate(raw_faces):
        fv = [verts_mm[i] for i in rf['v_idx']]
        n = len(fv)
        for ei in range(n):
            em_init.setdefault(make_3d_edge_key(fv[ei], fv[(ei + 1) % n]), []).append(fi)

    visited = set()
    for start_fi in range(len(raw_faces)):
        if start_fi in visited:
            continue
        comp = []
        q = [start_fi]
        visited.add(start_fi)
        while q:
            c = q.pop(0)
            comp.append(c)
            fv = [verts_mm[i] for i in raw_faces[c]['v_idx']]
            for ei in range(len(fv)):
                ek = make_3d_edge_key(fv[ei], fv[(ei + 1) % len(fv)])
                for nb in em_init[ek]:
                    if nb not in visited:
                        visited.add(nb)
                        q.append(nb)

        best_seed, best_abs, flip_seed = comp[0], -1.0, False
        for c in comp:
            fv = [verts_mm[i] for i in raw_faces[c]['v_idx']]
            _, norm = project_face_to_2d(fv)
            fc = (sum(v[0] for v in fv) / len(fv), sum(v[1] for v in fv) / len(fv), sum(v[2] for v in fv) / len(fv))
            dot = (fc[0] - cx_m) * norm[0] + (fc[1] - cy_m) * norm[1] + (fc[2] - cz_m) * norm[2]
            if abs(dot) > best_abs:
                best_abs = abs(dot)
                best_seed = c
                flip_seed = (dot < 0)

        if flip_seed:
            raw_faces[best_seed]['v_idx'].reverse()
            if raw_faces[best_seed]['uv_idx']:
                raw_faces[best_seed]['uv_idx'].reverse()

        comp_vis = {best_seed}
        cq = [best_seed]
        while cq:
            curr = cq.pop(0)
            cv = [verts_mm[i] for i in raw_faces[curr]['v_idx']]
            for ei in range(len(cv)):
                p1, p2 = cv[ei], cv[(ei + 1) % len(cv)]
                ek = make_3d_edge_key(p1, p2)
                for nb in em_init[ek]:
                    if nb not in comp_vis:
                        nv = [verts_mm[i] for i in raw_faces[nb]['v_idx']]
                        for nei in range(len(nv)):
                            if make_3d_edge_key(nv[nei], nv[(nei + 1) % len(nv)]) == ek:
                                if _dist3d(p1, nv[nei]) < 1e-1:
                                    raw_faces[nb]['v_idx'].reverse()
                                    if raw_faces[nb]['uv_idx']:
                                        raw_faces[nb]['uv_idx'].reverse()
                                break
                        comp_vis.add(nb)
                        cq.append(nb)

    face_data = []
    for fi, rf in enumerate(raw_faces):
        fv = [verts_mm[i] for i in rf['v_idx']]
        poly2d, norm = project_face_to_2d(fv)
        n_pts = len(fv)
        eks = [make_3d_edge_key(fv[i], fv[(i + 1) % n_pts]) for i in range(n_pts)]
        emids = [((fv[i][0] + fv[(i + 1) % n_pts][0]) * 0.5,
                  (fv[i][1] + fv[(i + 1) % n_pts][1]) * 0.5,
                  (fv[i][2] + fv[(i + 1) % n_pts][2]) * 0.5) for i in range(n_pts)]
        fc = (sum(v[0] for v in fv) / n_pts, sum(v[1] for v in fv) / n_pts, sum(v[2] for v in fv) / n_pts)
        uvs = [model.uvs[ui] for ui in rf['uv_idx']] if rf['uv_idx'] else []
        face_data.append({
            'fi': fi, 'verts_3d': fv, 'poly2d': poly2d,
            'edge_keys': eks, 'edge_mids': emids, 'center_3d': fc,
            'normal': norm, 'uvs': uvs
        })

    edge_map = {}
    for fd in face_data:
        for ei, ek in enumerate(fd['edge_keys']):
            edge_map.setdefault(ek, []).append((fd['fi'], ei))

    exact_seams = []
    for ek, lst in edge_map.items():
        if len(lst) == 2:
            (fa, ea), (fb, eb) = lst
            na, nb = face_data[fa]['normal'], face_data[fb]['normal']
            ca, cb = face_data[fa]['center_3d'], face_data[fb]['center_3d']
            d_ab = (cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2])
            s = na[0] * d_ab[0] + na[1] * d_ab[1] + na[2] * d_ab[2]
            ftype = 'valley' if s > 1e-3 else 'mountain'
            dihedral = math.acos(max(-1.0, min(1.0, na[0] * nb[0] + na[1] * nb[1] + na[2] * nb[2])))
            p1 = face_data[fa]['poly2d'][ea]
            p2 = face_data[fa]['poly2d'][(ea + 1) % len(face_data[fa]['poly2d'])]
            elen = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            same_deg = (len(face_data[fa]['verts_3d']) == len(face_data[fb]['verts_3d']))
            exact_seams.append({
                'ek': ek, 'fa': fa, 'ea': ea, 'fb': fb, 'eb': eb,
                'ftype': ftype, 'dihedral': dihedral, 'elen': elen, 'same_deg': same_deg
            })

    # Prioritize continuous ring/strip joints before attaching bases/caps, then longer edges
    exact_seams.sort(key=lambda s: (0 if s['same_deg'] else 1, -round(s['elen'], 1), round(s['dihedral'], 2)))

    islands = {
        fd['fi']: {'faces': {fd['fi']: fd['poly2d']}, 'internal_keys': {}}
        for fd in face_data
    }
    parent = {fd['fi']: fd['fi'] for fd in face_data}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    max_w, max_h = page_w - 2.0 * margin, page_h - 2.0 * margin

    for seam in exact_seams:
        ra, rb = find(seam['fa']), find(seam['fb'])
        if ra == rb:
            continue
        isl_a, isl_b = islands[ra], islands[rb]
        poly_a = isl_a['faces'][seam['fa']]
        poly_b = isl_b['faces'][seam['fb']]
        ea, eb = seam['ea'], seam['eb']
        a1, a2 = poly_a[ea], poly_a[(ea + 1) % len(poly_a)]
        p3_1 = face_data[seam['fa']]['verts_3d'][ea]

        xform = _compute_hinge_transform(poly_a, a1, a2, p3_1, poly_b, face_data[seam['fb']]['verts_3d'], eb)
        trans_b = {fi: [xform(pt) for pt in p] for fi, p in isl_b['faces'].items()}

        # Rule 1: Zero face-to-face overlap
        if any(polys_strictly_overlap(pa, pb, tol=0.1) for pa in isl_a['faces'].values() for pb in trans_b.values()):
            continue

        merged_faces = dict(isl_a['faces'])
        merged_faces.update(trans_b)
        merged_ik = dict(isl_a['internal_keys'])
        merged_ik.update(isl_b['internal_keys'])
        merged_ik[seam['ek']] = seam['ftype']

        # Rule 2: Circular closure & flap non-overlap check
        seam_edges_in_m = {}
        for fi, poly in merged_faces.items():
            for ei in range(len(poly)):
                ek = face_data[fi]['edge_keys'][ei]
                if ek in merged_ik:
                    continue
                if len(edge_map.get(ek, [])) == 2:
                    seam_edges_in_m.setdefault(ek, []).append((fi, ei, poly))

        all_flaps_pts = []
        placed_test_flaps = []
        flap_blocked = False
        for ek, sides in seam_edges_in_m.items():
            can_place_one = False
            for (fi_s, ei_s, poly_s) in sides:
                p1, p2 = poly_s[ei_s], poly_s[(ei_s + 1) % len(poly_s)]
                L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                fp = generate_outward_flap(poly_s, p1, p2, depth=min(flap_size_mm, L * 0.45))
                hits_face = any(polys_strictly_overlap(fp, fpoly, tol=0.1) for fpoly in merged_faces.values())
                hits_flap = any(polys_strictly_overlap(fp, tfp, tol=0.1) for tfp in placed_test_flaps)
                if not hits_face and not hits_flap:
                    can_place_one = True
                    placed_test_flaps.append(fp)
                    all_flaps_pts.extend(fp)
                    break
            if len(sides) == 2 and not can_place_one:
                flap_blocked = True
                break
        if flap_blocked:
            continue

        # Rule 3: Printable US Letter / A4 margin fit check over rotations
        pts_total = [pt for p in merged_faces.values() for pt in p] + all_flaps_pts
        fits_page = False
        for ang in range(0, 180, 5):
            rad = math.radians(ang)
            ca, sa = math.cos(rad), math.sin(rad)
            xs = [p[0] * ca - p[1] * sa for p in pts_total]
            ys = [p[0] * sa + p[1] * ca for p in pts_total]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if (w <= max_w and h <= max_h) or (h <= max_w and w <= max_h):
                fits_page = True
                break
        if not fits_page:
            continue

        islands[ra] = {'faces': merged_faces, 'internal_keys': merged_ik}
        del islands[rb]
        parent[rb] = ra

    # Convert merged islands into PaperParts
    parts = []
    for _, isl in islands.items():
        pts_all = [pt for p in isl['faces'].values() for pt in p]
        best_ang, best_score = 0, float('inf')
        for ang in range(0, 180, 2):
            rad = math.radians(ang)
            ca, sa = math.cos(rad), math.sin(rad)
            xs = [p[0] * ca - p[1] * sa for p in pts_all]
            ys = [p[0] * sa + p[1] * ca for p in pts_all]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            sc = (w * h + (0 if h >= w else 500)) if (w <= max_w - 11.0 and h <= max_h - 11.0) else (1e9 + w * h)
            if sc < best_score:
                best_score = sc
                best_ang = ang

        rad = math.radians(best_ang)
        ca, sa = math.cos(rad), math.sin(rad)
        rot_faces = {fi: [(p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca) for p in poly]
                     for fi, poly in isl['faces'].items()}

        sub_faces = [
            {
                'fi': fi, 'poly2d': poly,
                'edge_keys': face_data[fi]['edge_keys'],
                'edge_mids': face_data[fi]['edge_mids'],
                'uvs': face_data[fi]['uvs']
            }
            for fi, poly in rot_faces.items()
        ]
        outer_poly, outer_eks, outer_emids = _trace_island_boundary(sub_faces, isl['internal_keys'])

        part = PaperPart(part_id=len(parts) + 1, name=f"Island {len(parts) + 1}")
        part.poly2d = outer_poly
        part.edge_3d_keys = outer_eks
        part.edge_3d_mids = outer_emids
        part.sub_faces = sub_faces
        part._tex_img = tex_img

        seen_ik = set()
        for sf in sub_faces:
            n = len(sf['poly2d'])
            for ei in range(n):
                ek = sf['edge_keys'][ei]
                if ek in isl['internal_keys'] and ek not in seen_ik:
                    seen_ik.add(ek)
                    part.internal_folds.append(((sf['poly2d'][ei], sf['poly2d'][(ei + 1) % n]), isl['internal_keys'][ek]))

        if tex_img is not None:
            part.texture_crop = bake_island_texture(sub_faces, tex_img, dpi=300)
        parts.append(part)

    # Smart collision- & clutter-aware flap assignment on remaining external seams
    edge_catalog = []
    for p_idx, part in enumerate(parts):
        n_edges = len(part.poly2d)
        for e_idx in range(n_edges):
            edge_catalog.append({
                'part_idx': p_idx, 'edge_idx': e_idx,
                'p1': part.poly2d[e_idx], 'p2': part.poly2d[(e_idx + 1) % n_edges],
                'key_3d': part.edge_3d_keys[e_idx],
                'mid_3d': part.edge_3d_mids[e_idx]
            })

    matched_edges = set()
    seam_pairs = []
    for i in range(len(edge_catalog)):
        if i in matched_edges:
            continue
        for j in range(i + 1, len(edge_catalog)):
            if j in matched_edges:
                continue
            if edge_catalog[i]['key_3d'] == edge_catalog[j]['key_3d']:
                seam_pairs.append((i, j))
                matched_edges.add(i)
                matched_edges.add(j)
                break

    print(f"Edge-Joining Solver: {len(face_data)} faces -> {len(parts)} joined islands ({len(seam_pairs)} cut+glue seams).")

    def _owning_subface_poly(part_obj, edge_obj):
        p1, p2 = edge_obj['p1'], edge_obj['p2']
        for sf in part_obj.sub_faces:
            n = len(sf['poly2d'])
            for ei in range(n):
                if sf['edge_keys'][ei] == edge_obj['key_3d']:
                    q1, q2 = sf['poly2d'][ei], sf['poly2d'][(ei + 1) % n]
                    if (math.hypot(q1[0] - p1[0], q1[1] - p1[1]) < 0.1 and math.hypot(q2[0] - p2[0], q2[1] - p2[1]) < 0.1) or \
                       (math.hypot(q1[0] - p2[0], q1[1] - p2[1]) < 0.1 and math.hypot(q2[0] - p1[0], q2[1] - p1[1]) < 0.1):
                        return sf['poly2d']
        return part_obj.poly2d

    def _score_flap_side(part_obj, edge_obj):
        p1, p2 = edge_obj['p1'], edge_obj['p2']
        L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        depth = min(flap_size_mm, L * 0.45)
        sf_poly = _owning_subface_poly(part_obj, edge_obj)
        fp = generate_outward_flap(sf_poly, p1, p2, depth=depth, taper_angle=45.0)
        hits_face = any(polys_strictly_overlap(fp, sf['poly2d'], tol=0.05) for sf in part_obj.sub_faces)
        hits_flap = any(polys_strictly_overlap(fp, t['poly'], tol=0.02) for t in part_obj.tabs)
        tight_corner = any(_flaps_share_tight_corner(fp, (p1, p2), t['poly'], t['edge']) for t in part_obj.tabs)
        # Lower penalty is better: 0 = completely free, 1 = tight corner (< 92°), 100 = overlap
        penalty = (100 if hits_face or hits_flap else 0) + (5 if tight_corner else 0)
        return penalty, fp

    for seam_id, (idx_a, idx_b) in enumerate(seam_pairs, start=1):
        ea, eb = edge_catalog[idx_a], edge_catalog[idx_b]
        pa, pb = parts[ea['part_idx']], parts[eb['part_idx']]

        pen_a, fp_a = _score_flap_side(pa, ea)
        pen_b, fp_b = _score_flap_side(pb, eb)

        if pen_a < pen_b:
            use_a = True
        elif pen_b < pen_a:
            use_a = False
        else:
            use_a = (seam_id % 2 == 1)

        if use_a:
            tab_part, tab_edge, tab_fp = pa, ea, fp_a
            clean_part, clean_edge = pb, eb
        else:
            tab_part, tab_edge, tab_fp = pb, eb, fp_b
            clean_part, clean_edge = pa, ea

        # Guarantee zero corner bevel collision if sharing a sharp corner
        tab_fp = _miter_flap_against_tabs(tab_fp, (tab_edge['p1'], tab_edge['p2']), tab_part.tabs)
        tab_part.tabs.append({'edge': (tab_edge['p1'], tab_edge['p2']), 'poly': tab_fp, 'id': seam_id})

        cp1, cp2 = clean_edge['p1'], clean_edge['p2']
        sf_c = _owning_subface_poly(clean_part, clean_edge)
        nx, ny = compute_outward_normal(sf_c, cp1, cp2)
        clean_part.edge_labels.append({
            'pos': ((cp1[0] + cp2[0]) * 0.5 + 2.0 * nx, (cp1[1] + cp2[1]) * 0.5 + 2.0 * ny),
            'id': seam_id
        })

    return parts


def unfold_model_to_parts(model, target_scale=None, flap_size_mm=5.5, texture_path=None,
                          join_faces=False, paper_format="LETTER", margin=15.0):
    """
    Universal 3D Unroller:
      1. Automatically detects model scale (meters, cm, mm) if not specified.
      2. Projects each face to 2D preserving exact 3D lengths in millimeters.
      3. Crops UV texture subimages (or bakes multi-face affine textures when joining).
      4. Optionally joins faces across shared edges into connected non-overlapping islands.
      5. Applies alternating / collision-free outward glue tabs with configured flap size (mm).
    """
    if target_scale is None:
        target_scale = compute_auto_scale(model.vertices)

    # 1. Scale 3D vertices to real-world millimeters
    verts_mm = [(v[0] * target_scale, v[1] * target_scale, v[2] * target_scale) for v in model.vertices]

    # Resolve texture image
    tex_img = None
    tex_candidate = texture_path or getattr(model, 'texture_file', None)
    if tex_candidate and os.path.exists(tex_candidate):
        try:
            tex_img = Image.open(tex_candidate)
        except Exception as e:
            print(f"Notice: Could not load texture map ({e}).")

    if join_faces:
        pw, ph = (210.0, 297.0) if paper_format.upper() == "A4" else (215.9, 279.4)
        return _unfold_model_joined(model, verts_mm, tex_img, flap_size_mm=flap_size_mm,
                                    page_w=pw, page_h=ph, margin=margin)

    # 2. Project individual faces to 2D
    face_data = []
    for f_idx, f in enumerate(model.faces):
        f_verts_3d = [verts_mm[vi] for vi in f['v']]
        poly2d, normal = project_face_to_2d(f_verts_3d)

        # 3D edge keys and midpoints for this face
        n_pts = len(f_verts_3d)
        edge_keys_3d = [make_3d_edge_key(f_verts_3d[i], f_verts_3d[(i + 1) % n_pts]) for i in range(n_pts)]
        edge_mids_3d = [
            ((f_verts_3d[i][0] + f_verts_3d[(i + 1) % n_pts][0]) * 0.5,
             (f_verts_3d[i][1] + f_verts_3d[(i + 1) % n_pts][1]) * 0.5,
             (f_verts_3d[i][2] + f_verts_3d[(i + 1) % n_pts][2]) * 0.5)
            for i in range(n_pts)
        ]

        face_data.append({
            'face_idx': f_idx,
            'verts_3d': f_verts_3d,
            'poly2d': poly2d,
            'edge_keys': edge_keys_3d,
            'edge_mids': edge_mids_3d,
            'normal': normal
        })

    # 3. Create independent PaperPart for each face
    parts = []
    for fd in face_data:
        p = PaperPart(part_id=len(parts) + 1, name=f"Part {len(parts) + 1}")
        p.poly2d = list(fd['poly2d'])
        p.edge_3d_keys = list(fd['edge_keys'])
        p.edge_3d_mids = list(fd['edge_mids'])

        # Crop UV texture if available
        if tex_img is not None and fd['face_idx'] < len(model.faces):
            f_obj = model.faces[fd['face_idx']]
            if f_obj.get('vt'):
                W, H = tex_img.size
                uvs = [model.uvs[uvi] for uvi in f_obj['vt'] if uvi < len(model.uvs)]
                if uvs:
                    us = [uv[0] for uv in uvs]
                    vs = [uv[1] for uv in uvs]
                    x1 = max(0, min(W - 1, int(min(us) * W)))
                    x2 = max(x1 + 1, min(W, int(max(us) * W)))
                    y1 = max(0, min(H - 1, int((1.0 - max(vs)) * H)))
                    y2 = max(y1 + 1, min(H, int((1.0 - min(vs)) * H)))
                    crop = tex_img.crop((x1, y1, x2, y2))

                    poly_w = max(pt[0] for pt in p.poly2d) - min(pt[0] for pt in p.poly2d)
                    poly_h = max(pt[1] for pt in p.poly2d) - min(pt[1] for pt in p.poly2d)
                    crop_w, crop_h = crop.size
                    if (poly_w > poly_h and crop_w < crop_h) or (poly_w < poly_h and crop_w > crop_h):
                        crop = crop.rotate(90, expand=True)
                    p.texture_crop = crop

        parts.append(p)

    # 4. Global 3D Seam Matching across all parts
    edge_catalog = []
    for p_idx, part in enumerate(parts):
        poly = part.poly2d
        n_edges = len(poly)
        for e_idx in range(n_edges):
            p1 = poly[e_idx]
            p2 = poly[(e_idx + 1) % n_edges]
            ek = part.edge_3d_keys[e_idx] if e_idx < len(part.edge_3d_keys) else None
            emid = part.edge_3d_mids[e_idx] if e_idx < len(part.edge_3d_mids) else None
            edge_catalog.append({
                'part_idx': p_idx,
                'edge_idx': e_idx,
                'p1': p1,
                'p2': p2,
                'key_3d': ek,
                'mid_3d': emid
            })

    matched_edges = set()
    seam_pairs = []

    # Pass 1: Exact 3D edge key matching
    for i in range(len(edge_catalog)):
        if i in matched_edges:
            continue
        e_a = edge_catalog[i]
        for j in range(i + 1, len(edge_catalog)):
            if j in matched_edges:
                continue
            e_b = edge_catalog[j]
            if e_a['part_idx'] != e_b['part_idx'] and e_a['key_3d'] == e_b['key_3d']:
                seam_pairs.append((i, j))
                matched_edges.add(i)
                matched_edges.add(j)
                break

    # Pass 2: Proximity matching for architectural overhangs / eaves (within 10.0 mm)
    for i in range(len(edge_catalog)):
        if i in matched_edges:
            continue
        e_a = edge_catalog[i]
        best_j = None
        min_dist = 10.0  # mm threshold
        ma = e_a['mid_3d']
        if ma is not None:
            for j in range(len(edge_catalog)):
                if j == i or j in matched_edges:
                    continue
                e_b = edge_catalog[j]
                if e_a['part_idx'] == e_b['part_idx']:
                    continue
                mb = e_b['mid_3d']
                if mb is None:
                    continue
                dist = math.hypot(ma[0] - mb[0], math.hypot(ma[1] - mb[1], ma[2] - mb[2]))
                if dist < min_dist:
                    min_dist = dist
                    best_j = j
            if best_j is not None:
                seam_pairs.append((i, best_j))
                matched_edges.add(i)
                matched_edges.add(best_j)

    print(f"Identified {len(seam_pairs)} shared 3D seams across {len(parts)} parts.")

    # 5. Apply Strict Alternating Flap Assignment
    for seam_id, (idx_a, idx_b) in enumerate(seam_pairs, start=1):
        e_a = edge_catalog[idx_a]
        e_b = edge_catalog[idx_b]

        part_a = parts[e_a['part_idx']]
        part_b = parts[e_b['part_idx']]

        # Alternating rule: decide which side gets the outward tab
        if seam_id % 2 == 1:
            tab_edge, clean_edge = e_a, e_b
            tab_part, clean_part = part_a, part_b
        else:
            tab_edge, clean_edge = e_b, e_a
            tab_part, clean_part = part_b, part_a

        # Add outward glue flap with configured depth in mm
        p1, p2 = tab_edge['p1'], tab_edge['p2']
        edge_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        depth = min(flap_size_mm, edge_len * 0.45)
        flap_poly = generate_outward_flap(tab_part.poly2d, p1, p2, depth=depth, taper_angle=45.0)
        tab_part.tabs.append({
            'edge': (p1, p2),
            'poly': flap_poly,
            'id': seam_id
        })

        # Add matching edge label 2.0mm OUTSIDE on scrap paper margin
        cp1, cp2 = clean_edge['p1'], clean_edge['p2']
        nx, ny = compute_outward_normal(clean_part.poly2d, cp1, cp2)
        mx = (cp1[0] + cp2[0]) * 0.5 + 2.0 * nx
        my = (cp1[1] + cp2[1]) * 0.5 + 2.0 * ny
        clean_part.edge_labels.append({
            'pos': (mx, my),
            'id': seam_id
        })

    return parts


def rotate_part_geometry(part, angle_deg, rotate_texture=True):
    """Rotates all geometry in part by angle_deg (supports arbitrary degrees)."""
    if angle_deg % 360 == 0:
        return
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    if angle_deg % 90 == 0:
        cos_a = round(cos_a, 4)
        sin_a = round(sin_a, 4)

    def rot(pt):
        return (pt[0] * cos_a - pt[1] * sin_a, pt[0] * sin_a + pt[1] * cos_a)

    part.poly2d = [rot(pt) for pt in part.poly2d]
    for sf in getattr(part, 'sub_faces', []):
        sf['poly2d'] = [rot(pt) for pt in sf['poly2d']]
    for t in part.tabs:
        t['edge'] = (rot(t['edge'][0]), rot(t['edge'][1]))
        t['poly'] = [rot(pt) for pt in t['poly']]
    for l in part.edge_labels:
        l['pos'] = rot(l['pos'])
    part.internal_folds = [((rot(f[0][0]), rot(f[0][1])), f[1]) for f in part.internal_folds]
    if rotate_texture and getattr(part, 'texture_crop', None) is not None:
        if angle_deg % 90 == 0:
            part.texture_crop = part.texture_crop.rotate(-angle_deg, expand=True)
        elif getattr(part, 'sub_faces', None) and getattr(part, '_tex_img', None) is not None:
            part.texture_crop = bake_island_texture(part.sub_faces, part._tex_img, dpi=300)


def get_part_outer_cut_polygon(part):
    """
    Constructs the exact combined outer cut perimeter:
    - Along clean edges without tabs: p1 -> p2
    - Along edges with attached tabs: p1 -> t1 -> t2 -> p2
    Ensures tabs remain attached and the shared base edge is strictly a fold line.
    """
    poly = part.poly2d
    n = len(poly)
    outer_pts = []

    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]

        # Check if a tab is attached to this edge
        tab_found = None
        for t in part.tabs:
            te1, te2 = t['edge']
            d1 = math.hypot(te1[0] - p1[0], te1[1] - p1[1]) + math.hypot(te2[0] - p2[0], te2[1] - p2[1])
            d2 = math.hypot(te1[0] - p2[0], te1[1] - p2[1]) + math.hypot(te2[0] - p1[0], te2[1] - p1[1])
            if d1 < 1e-2 or d2 < 1e-2:
                tab_found = t
                break

        if tab_found is not None:
            t_poly = tab_found['poly']
            if math.hypot(t_poly[0][0] - p1[0], t_poly[0][1] - p1[1]) < 1e-2:
                outer_pts.extend([p1, t_poly[1], t_poly[2]])
            else:
                outer_pts.extend([p1, t_poly[2], t_poly[1]])
        else:
            outer_pts.append(p1)

    return outer_pts


# =============================================================================
# 4. OPTIMAL BIN PACKING ONTO PAGES (US LETTER / A4) WITH ROTATION
# =============================================================================

def _part_collision_shapes(part):
    """Returns physical convex polygons (sub-faces or poly2d + tabs + label boxes) with precomputed AABBs."""
    polys = [sf['poly2d'] for sf in part.sub_faces] if getattr(part, 'sub_faces', None) else [part.poly2d]
    for t in part.tabs:
        polys.append(t['poly'])
    for l in part.edge_labels:
        lx, ly = l['pos']
        polys.append([(lx - 1.2, ly - 1.2), (lx + 1.2, ly - 1.2), (lx + 1.2, ly + 1.2), (lx - 1.2, ly + 1.2)])
    return [(p, (min(pt[0] for pt in p), min(pt[1] for pt in p), max(pt[0] for pt in p), max(pt[1] for pt in p)))
            for p in polys]


def _parts_clear(shapes_a, box_a, shapes_b, box_b, part_gap=5.0, exact=False):
    if (box_a[2] + part_gap <= box_b[0] or box_a[0] >= box_b[2] + part_gap or
            box_a[3] + part_gap <= box_b[1] or box_a[1] >= box_b[3] + part_gap):
        return True
    if not exact:
        return False
    for pa, ba in shapes_a:
        for pb, bb in shapes_b:
            if (ba[2] + part_gap <= bb[0] or ba[0] >= bb[2] + part_gap or
                    ba[3] + part_gap <= bb[1] or ba[1] >= bb[3] + part_gap):
                continue
            if poly_min_dist(pa, pb) < part_gap:
                return False
    return True


def pack_parts_onto_pages(parts, page_w, page_h, margin=15.0, part_gap=5.0):
    """
    Optimal 2D Bin Packing to minimize sheet count:
      - Tests rotations (0°, 90°, 180°, 270°, plus 45° increments for curved multi-face islands).
      - Uses polygon-level distance clearance for concave/curved islands to nest inside pockets.
      - Enforces strict >= 15.0 mm margin from all 4 page edges.
      - Enforces strict >= 5.0 mm clearance between all adjacent parts (including flaps/labels).
      - Reuses existing sheets first before allocating a new sheet.
      - Never scales or resizes any part.
    """
    min_x_limit = margin
    min_y_limit = margin
    max_x_limit = page_w - margin
    max_y_limit = page_h - margin

    sorted_parts = sorted(
        parts,
        key=lambda p: max(p.compute_bounds()[2] - p.compute_bounds()[0],
                          p.compute_bounds()[3] - p.compute_bounds()[1]),
        reverse=True
    )

    sheets = []  # list of lists of (part, [min_x, min_y, max_x, max_y], shapes)

    for part in sorted_parts:
        is_island = bool(getattr(part, 'sub_faces', None))
        rot_candidates = [0, 90, 180, 270, 45, 135, 225, 315] if is_island else [0, 90, 180, 270]
        orig_snap = part.snapshot_geometry()

        best_sheet_idx = None
        best_pos = None
        best_rot = None
        best_score = float('inf')

        for rot in rot_candidates:
            part.restore_geometry(orig_snap)
            rotate_part_geometry(part, rot, rotate_texture=False)
            bx1, by1, bx2, by2 = part.compute_bounds()
            pw = bx2 - bx1
            ph = by2 - by1

            if pw > (max_x_limit - min_x_limit) or ph > (max_y_limit - min_y_limit):
                continue

            base_shapes = _part_collision_shapes(part) if is_island else None

            for s_idx, sheet in enumerate(sheets):
                has_island_on_sheet = is_island or any(bool(getattr(op, 'sub_faces', None)) for op, _, _ in sheet)
                cur_shapes = base_shapes if base_shapes is not None else (_part_collision_shapes(part) if has_island_on_sheet else None)

                candidates = [(min_x_limit, min_y_limit)]
                for _, pb, _ in sheet:
                    candidates.append((pb[2] + part_gap, pb[1]))
                    candidates.append((pb[2] + part_gap, min_y_limit))
                    candidates.append((pb[0], pb[3] + part_gap))
                    candidates.append((min_x_limit, pb[3] + part_gap))

                # Add concave pocket grid scan when nesting alongside curved multi-face islands
                if has_island_on_sheet:
                    gx_max = max_x_limit - pw
                    gy_max = max_y_limit - ph
                    gx = min_x_limit
                    while gx <= gx_max + 0.01:
                        gy = min_y_limit
                        while gy <= gy_max + 0.01:
                            candidates.append((gx, gy))
                            gy += 8.0
                        gx += 8.0

                for cx, cy in candidates:
                    target_x2 = cx + pw
                    target_y2 = cy + ph
                    if target_x2 > max_x_limit or target_y2 > max_y_limit:
                        continue

                    dx_c, dy_c = cx - bx1, cy - by1
                    cand_box = (cx, cy, target_x2, target_y2)
                    cand_shapes = (
                        [([(pt[0] + dx_c, pt[1] + dy_c) for pt in p],
                          (b[0] + dx_c, b[1] + dy_c, b[2] + dx_c, b[3] + dy_c))
                         for p, b in cur_shapes]
                        if has_island_on_sheet else None
                    )

                    collides = False
                    for _, pb, p_shapes in sheet:
                        if not _parts_clear(cand_shapes, cand_box, p_shapes, pb, part_gap=part_gap, exact=has_island_on_sheet):
                            collides = True
                            break

                    if not collides:
                        score = s_idx * 1000000 + target_y2 * 1000 + target_x2 + (100 if rot % 90 != 0 else 0)
                        if score < best_score:
                            best_score = score
                            best_sheet_idx = s_idx
                            best_pos = (cx, cy)
                            best_rot = rot

        part.restore_geometry(orig_snap)

        if best_sheet_idx is not None:
            rotate_part_geometry(part, best_rot, rotate_texture=True)
            bx1, by1, bx2, by2 = part.compute_bounds()
            part.translate(best_pos[0] - bx1, best_pos[1] - by1)
            final_box = part.compute_bounds()
            sheets[best_sheet_idx].append((part, final_box, _part_collision_shapes(part)))
        else:
            best_rot = 0
            for rot in rot_candidates:
                part.restore_geometry(orig_snap)
                rotate_part_geometry(part, rot, rotate_texture=False)
                bx1, by1, bx2, by2 = part.compute_bounds()
                if (bx2 - bx1) <= (max_x_limit - min_x_limit) and (by2 - by1) <= (max_y_limit - min_y_limit):
                    best_rot = rot
                    break

            part.restore_geometry(orig_snap)
            rotate_part_geometry(part, best_rot, rotate_texture=True)
            bx1, by1, bx2, by2 = part.compute_bounds()
            part.translate(min_x_limit - bx1, min_y_limit - by1)
            final_box = part.compute_bounds()
            sheets.append([(part, final_box, _part_collision_shapes(part))])

    return [[item[0] for item in sheet] for sheet in sheets]


# =============================================================================
# 5. MULTI-FORMAT EXPORT (PDF, SVG, 300 DPI PNG)
# =============================================================================

def export_svg_page(page_parts, page_num, total_pages, page_w, page_h, filepath,
                    variation=1, footer_text=None):
    """Generates precision vector SVG for cutting plotters and manual craft."""
    svg = []
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_w}mm" height="{page_h}mm" '
               f'viewBox="0 0 {page_w} {page_h}">')

    # Defs: Cross-hatch diamond pattern for glue tabs
    svg.append('  <defs>')
    svg.append('    <pattern id="flap_crosshatch" width="2.2" height="2.2" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">')
    svg.append('      <rect width="2.2" height="2.2" fill="#E6E6E6"/>')
    svg.append('      <line x1="0" y1="0" x2="0" y2="2.2" stroke="#B8B8B8" stroke-width="0.32"/>')
    svg.append('      <line x1="0" y1="0" x2="2.2" y2="0" stroke="#B8B8B8" stroke-width="0.32"/>')
    svg.append('    </pattern>')
    svg.append('  </defs>')

    # Optical registration marks (5x5mm black squares for optical plotter sensors)
    svg.append('  <!-- OPTICAL REGISTRATION MARKS -->')
    svg.append('  <g id="REGISTRATION_MARKS" fill="#000000">')
    svg.append(f'    <rect x="{15.0}" y="{3.0}" width="5" height="5"/>')
    svg.append(f'    <rect x="{page_w - 20.0}" y="{3.0}" width="5" height="5"/>')
    svg.append('  </g>')

    # Header & Legend
    svg.append('  <!-- HEADER & LEGEND SWATCHES -->')
    svg.append(f'  <text x="24" y="6.8" font-family="Helvetica, Arial, sans-serif" font-weight="bold" font-size="3.2" fill="#111111">'
               f'3D Papercraft Template (Pepakura Alternative) — Page {page_num}/{total_pages}</text>')

    if variation in (1, 3):
        svg.append('  <g id="LEGEND" transform="translate(24, 11.2)" font-family="Helvetica, Arial, sans-serif" font-size="2.0" fill="#333333">')
        svg.append('    <line x1="0" y1="0" x2="8" y2="0" stroke="#000000" stroke-width="0.4"/>')
        svg.append('    <text x="10" y="0.7">Solid: Cut Line</text>')
        svg.append('    <line x1="32" y1="0" x2="40" y2="0" stroke="#D32F2F" stroke-width="0.4" stroke-dasharray="1.5,0.7"/>')
        svg.append('    <text x="42" y="0.7">Dashed Red: Mountain Fold (_ _ _)</text>')
        svg.append('    <line x1="88" y1="0" x2="96" y2="0" stroke="#1976D2" stroke-width="0.4" stroke-dasharray="2.0,0.7,0.5,0.7"/>')
        svg.append('    <text x="98" y="0.7">Dash-Dot Blue: Valley Fold (_ . _ .)</text>')
        svg.append('    <rect x="142" y="-1.8" width="6" height="3.5" fill="url(#flap_crosshatch)" stroke="#777777" stroke-width="0.25"/>')
        svg.append('    <text x="150" y="0.7">Flap (Cross-lined)</text>')
        svg.append('  </g>')

    # Layer: Facet Backgrounds
    fill_color = "#FFFFFF" if variation in (3, 4) else "#FBF9F5"
    svg.append('  <!-- PART FACETS -->')
    svg.append(f'  <g id="FACETS" fill="{fill_color}" stroke="none">')
    for part in page_parts:
        pts_str = " ".join(f"{p[0]:.2f},{p[1]:.2f}" for p in part.poly2d)
        svg.append(f'    <polygon points="{pts_str}"/>')
    svg.append('  </g>')

    # Layer: Glue Flaps (cross-hatched with faint grey fill)
    svg.append('  <!-- GLUE TABS (OUTWARD 45° WITH CROSS-HATCH PATTERN) -->')
    svg.append('  <g id="GLUE_TABS" fill="url(#flap_crosshatch)" stroke="none">')
    for part in page_parts:
        for t in part.tabs:
            pts_str = " ".join(f"{p[0]:.2f},{p[1]:.2f}" for p in t['poly'])
            svg.append(f'    <polygon points="{pts_str}"/>')
    svg.append('  </g>')

    # Layer: Internal Fold Lines (Variations 1 and 3 only)
    if variation in (1, 3):
        svg.append('  <!-- INTERNAL CREASE LINES -->')
        svg.append('  <g id="FOLD_LINES">')
        for part in page_parts:
            # Baseline of tabs is strictly a dashed mountain fold
            for t in part.tabs:
                p1, p2 = t['edge']
                svg.append(f'    <line x1="{p1[0]:.2f}" y1="{p1[1]:.2f}" x2="{p2[0]:.2f}" y2="{p2[1]:.2f}" '
                           f'stroke="#D32F2F" stroke-width="0.4" stroke-dasharray="1.5,0.7"/>')
            for (p1, p2), ftype in part.internal_folds:
                if ftype == 'valley':
                    svg.append(f'    <line x1="{p1[0]:.2f}" y1="{p1[1]:.2f}" x2="{p2[0]:.2f}" y2="{p2[1]:.2f}" '
                               f'stroke="#1976D2" stroke-width="0.4" stroke-dasharray="2.0,0.7,0.5,0.7"/>')
                else:
                    svg.append(f'    <line x1="{p1[0]:.2f}" y1="{p1[1]:.2f}" x2="{p2[0]:.2f}" y2="{p2[1]:.2f}" '
                               f'stroke="#D32F2F" stroke-width="0.4" stroke-dasharray="1.5,0.7"/>')
        svg.append('  </g>')

    # Layer: Cut Lines (Solid Black 0.4mm around combined outer contour of part + flaps)
    svg.append('  <!-- CUT OUTLINES -->')
    svg.append('  <g id="CUT_LINES" fill="none" stroke="#000000" stroke-width="0.4">')
    for part in page_parts:
        outer_pts = get_part_outer_cut_polygon(part)
        pts_str = " ".join(f"{p[0]:.2f},{p[1]:.2f}" for p in outer_pts)
        svg.append(f'    <polygon points="{pts_str}"/>')
    svg.append('  </g>')

    # Layer: Edge ID Labels (Variations 1 and 3 only)
    if variation in (1, 3):
        svg.append('  <!-- EDGE ID NUMBERS (PROTECTED BADGE ON TABS, EXTERIOR IN SCRAP MARGIN) -->')
        svg.append('  <g id="EDGE_IDS" font-family="Helvetica, Arial, sans-serif" font-size="2.2" font-weight="bold" text-anchor="middle" dominant-baseline="central">')
        for part in page_parts:
            # Flap IDs
            for t in part.tabs:
                cx = sum(p[0] for p in t['poly']) / 4.0
                cy = sum(p[1] for p in t['poly']) / 4.0
                svg.append(f'    <circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.2" fill="#E6E6E6" stroke="#999999" stroke-width="0.2"/>')
                svg.append(f'    <text x="{cx:.2f}" y="{cy:.2f}" fill="#111111">{t["id"]}</text>')
            # Exterior Mating Edge IDs
            for l in part.edge_labels:
                lx, ly = l['pos']
                svg.append(f'    <text x="{lx:.2f}" y="{ly:.2f}" fill="#222222">{l["id"]}</text>')
        svg.append('  </g>')

    # Optional centered bottom margin footer (inside 15mm bottom margin)
    if footer_text:
        import html
        safe_footer = html.escape(str(footer_text))
        svg.append('  <!-- FOOTER BRANDING -->')
        svg.append(f'  <text x="{page_w * 0.5:.2f}" y="{page_h - 5.0:.2f}" font-family="Helvetica, Arial, sans-serif" '
                   f'font-weight="bold" font-size="4.94" text-anchor="middle" fill="#111111">{safe_footer}</text>')

    svg.append('</svg>')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(svg))


def export_pdf_pages(pages, page_w, page_h, filepath, variation=1, footer_text=None):
    """Generates multi-page vector PDF in exact mm coordinates."""
    c = canvas.Canvas(filepath, pagesize=(page_w * MM_TO_PT, page_h * MM_TO_PT))
    total_pages = len(pages)

    for p_idx, page_parts in enumerate(pages):
        page_num = p_idx + 1

        # Optical registration marks (5x5 mm black squares)
        c.setFillColor(colors.black)
        c.rect(15.0 * MM_TO_PT, (page_h - 8.0) * MM_TO_PT, 5.0 * MM_TO_PT, 5.0 * MM_TO_PT, fill=1, stroke=0)
        c.rect((page_w - 20.0) * MM_TO_PT, (page_h - 8.0) * MM_TO_PT, 5.0 * MM_TO_PT, 5.0 * MM_TO_PT, fill=1, stroke=0)

        # Header Title
        c.setFont("Helvetica-Bold", 9)
        c.drawString(24.0 * MM_TO_PT, (page_h - 6.8) * MM_TO_PT,
                     f"3D Papercraft Template (Pepakura Alternative) — Page {page_num}/{total_pages}")

        # Legend (Variations 1 and 3)
        if variation in (1, 3):
            leg_y = (page_h - 11.2) * MM_TO_PT
            c.setFont("Helvetica", 6.0)

            # Solid Cut Line
            c.setStrokeColor(colors.black)
            c.setLineWidth(1.1)
            c.setDash([])
            c.line(24 * MM_TO_PT, leg_y, 32 * MM_TO_PT, leg_y)
            c.drawString(34 * MM_TO_PT, leg_y - 2, "Solid: Cut Line")

            # Dashed Red Mountain Fold
            c.setStrokeColor(colors.HexColor('#D32F2F'))
            c.setLineWidth(1.1)
            c.setDash([4.5, 2.0])
            c.line(56 * MM_TO_PT, leg_y, 64 * MM_TO_PT, leg_y)
            c.drawString(66 * MM_TO_PT, leg_y - 2, "Dashed Red: Mountain Fold (_ _ _)")

            # Dash-Dot Blue Valley Fold
            c.setStrokeColor(colors.HexColor('#1976D2'))
            c.setLineWidth(1.1)
            c.setDash([6.0, 2.0, 1.5, 2.0])
            c.line(112 * MM_TO_PT, leg_y, 120 * MM_TO_PT, leg_y)
            c.drawString(122 * MM_TO_PT, leg_y - 2, "Dash-Dot Blue: Valley Fold (_ . _ .)")

            # Flap swatch
            c.setDash([])
            c.setStrokeColor(colors.HexColor('#777777'))
            c.setLineWidth(0.7)
            c.setFillColor(colors.HexColor('#E6E6E6'))
            c.rect(166 * MM_TO_PT, leg_y - 2.5, 6 * MM_TO_PT, 3.5 * MM_TO_PT, fill=1, stroke=1)
            c.setFillColor(colors.HexColor('#333333'))
            c.drawString(174 * MM_TO_PT, leg_y - 2, "Flap (Cross-lined)")

        # Render Parts
        for part in page_parts:
            # 1. Facet fill / Texture Artwork
            if variation in (1, 2) and getattr(part, 'texture_crop', None) is not None:
                c.saveState()
                clip_p = c.beginPath()
                clip_p.moveTo(part.poly2d[0][0] * MM_TO_PT, (page_h - part.poly2d[0][1]) * MM_TO_PT)
                for pt in part.poly2d[1:]:
                    clip_p.lineTo(pt[0] * MM_TO_PT, (page_h - pt[1]) * MM_TO_PT)
                clip_p.close()
                c.clipPath(clip_p, stroke=0)

                min_x, min_y, max_x, max_y = part.compute_poly_bounds()
                img_x = min_x * MM_TO_PT
                img_y = (page_h - max_y) * MM_TO_PT
                img_w = (max_x - min_x) * MM_TO_PT
                img_h = (max_y - min_y) * MM_TO_PT
                c.drawInlineImage(part.texture_crop, img_x, img_y, img_w, img_h)
                c.restoreState()
            else:
                fill_c = colors.white if variation in (3, 4) else colors.HexColor('#FAFAFA')
                c.setFillColor(fill_c)
                p = c.beginPath()
                p.moveTo(part.poly2d[0][0] * MM_TO_PT, (page_h - part.poly2d[0][1]) * MM_TO_PT)
                for pt in part.poly2d[1:]:
                    p.lineTo(pt[0] * MM_TO_PT, (page_h - pt[1]) * MM_TO_PT)
                p.close()
                c.drawPath(p, fill=1, stroke=0)

            # 2. Glue Tabs (faint grey with cross-hatch pattern, fill only without outline)
            for t in part.tabs:
                c.saveState()
                c.setFillColor(colors.HexColor('#E6E6E6'))
                c.setStrokeColor(colors.HexColor('#000000'))
                c.setLineWidth(0.8)
                c.setDash([])

                tp = c.beginPath()
                tp.moveTo(t['poly'][0][0] * MM_TO_PT, (page_h - t['poly'][0][1]) * MM_TO_PT)
                for pt in t['poly'][1:]:
                    tp.lineTo(pt[0] * MM_TO_PT, (page_h - pt[1]) * MM_TO_PT)
                tp.close()
                c.drawPath(tp, fill=1, stroke=0)

                # Clip path for cross-hatch pattern inside flap
                c.clipPath(tp, stroke=0)
                c.setStrokeColor(colors.HexColor('#B8B8B8'))
                c.setLineWidth(0.7)
                min_x = min(pt[0] for pt in t['poly']) * MM_TO_PT - 10
                max_x = max(pt[0] for pt in t['poly']) * MM_TO_PT + 10
                min_y = (page_h - max(pt[1] for pt in t['poly'])) * MM_TO_PT - 10
                max_y = (page_h - min(pt[1] for pt in t['poly'])) * MM_TO_PT + 10
                step = 2.2 * MM_TO_PT
                s = min_x - (max_y - min_y)
                while s < max_x + (max_y - min_y):
                    c.line(s, min_y, s + (max_y - min_y), max_y)
                    c.line(s, max_y, s + (max_y - min_y), min_y)
                    s += step
                c.restoreState()

            # 3. Internal Fold Creases (Variations 1 and 3)
            if variation in (1, 3):
                # Tab base creases - strictly dashed red mountain fold
                c.setStrokeColor(colors.HexColor('#D32F2F'))
                c.setLineWidth(1.2)
                c.setDash([5.0, 2.5])
                for t in part.tabs:
                    p1, p2 = t['edge']
                    c.line(p1[0] * MM_TO_PT, (page_h - p1[1]) * MM_TO_PT,
                           p2[0] * MM_TO_PT, (page_h - p2[1]) * MM_TO_PT)

                # Compound internal folds
                for (p1, p2), ftype in part.internal_folds:
                    if ftype == 'valley':
                        c.setStrokeColor(colors.HexColor('#1976D2'))
                        c.setLineWidth(1.2)
                        c.setDash([6.0, 2.0, 1.5, 2.0])
                    else:
                        c.setStrokeColor(colors.HexColor('#D32F2F'))
                        c.setLineWidth(1.2)
                        c.setDash([5.0, 2.5])
                    c.line(p1[0] * MM_TO_PT, (page_h - p1[1]) * MM_TO_PT,
                           p2[0] * MM_TO_PT, (page_h - p2[1]) * MM_TO_PT)

            # 4. Cut Outline (Solid Black around get_part_outer_cut_polygon)
            c.setStrokeColor(colors.black)
            c.setLineWidth(1.1)
            c.setDash([])
            out_p = c.beginPath()
            outer_pts = get_part_outer_cut_polygon(part)
            out_p.moveTo(outer_pts[0][0] * MM_TO_PT, (page_h - outer_pts[0][1]) * MM_TO_PT)
            for pt in outer_pts[1:]:
                out_p.lineTo(pt[0] * MM_TO_PT, (page_h - pt[1]) * MM_TO_PT)
            out_p.close()
            c.drawPath(out_p, fill=0, stroke=1)

            # 5. Edge ID Numbers (Variations 1 and 3)
            if variation in (1, 3):
                c.setFont("Helvetica-Bold", 6.0)
                # Tab Badges
                for t in part.tabs:
                    cx = sum(pt[0] for pt in t['poly']) / 4.0
                    cy = sum(pt[1] for pt in t['poly']) / 4.0
                    pt_x = cx * MM_TO_PT
                    pt_y = (page_h - cy) * MM_TO_PT
                    c.setFillColor(colors.HexColor('#E6E6E6'))
                    c.setStrokeColor(colors.HexColor('#999999'))
                    c.setLineWidth(0.5)
                    c.circle(pt_x, pt_y, 2.2 * MM_TO_PT, fill=1, stroke=1)
                    c.setFillColor(colors.HexColor('#111111'))
                    c.drawCentredString(pt_x, pt_y - 2.0, str(t['id']))

                # Exterior Cut Edge IDs
                c.setFillColor(colors.HexColor('#222222'))
                for l in part.edge_labels:
                    lx, ly = l['pos']
                    c.drawCentredString(lx * MM_TO_PT, (page_h - ly) * MM_TO_PT - 2.0, str(l['id']))

        # Optional centered bottom margin footer (Bold 14 pt inside 15mm bottom margin)
        if footer_text:
            c.setFillColor(colors.HexColor('#111111'))
            c.setFont("Helvetica-Bold", 14)
            c.drawCentredString((page_w * 0.5) * MM_TO_PT, 5.0 * MM_TO_PT, str(footer_text))

        c.showPage()

    c.save()


def render_pdf_to_300dpi_pngs(pdf_path, output_dir, prefix):
    """Renders PDF pages to crisp 300 DPI PNG images (2550 x 3300 px for Letter)."""
    doc = fitz.open(pdf_path)
    png_paths = []
    zoom = 300.0 / 72.0
    mat = fitz.Matrix(zoom, zoom)

    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out_png = os.path.join(output_dir, f"{prefix}_page{i + 1}_300dpi.png")
        pix.save(out_png)
        png_paths.append(out_png)
        print(f"  -> Generated 300 DPI Sheet: {out_png} ({pix.width}x{pix.height} px)")

    doc.close()
    return png_paths


# =============================================================================
# 6. MAIN EXECUTION PIPELINE
# =============================================================================

def run_pipeline(input_model_path=None, texture_path=None, outdir="exports",
                 paper_format="LETTER", flap_size_mm=5.5, user_scale=None,
                 join_faces=False, footer_text=None):
    """Executes the complete universal 3D papercraft pipeline."""
    if paper_format.upper() == "A4":
        page_w = 210.0
        page_h = 297.0
    else:
        page_w = 215.9
        page_h = 279.4

    # 1. Load 3D Model
    if input_model_path and os.path.exists(input_model_path):
        print(f"Loading user 3D Model: {input_model_path}")
        model = Model3D.load_obj(input_model_path)
    else:
        print("Notice: No 3D model specified via --input. Generating default low-poly geometric model.")
        model = Model3D.create_default_model()

    # 2. Intelligent Auto-Scale Detection (Meters -> mm)
    scale = compute_auto_scale(model.vertices, user_scale=user_scale)

    # 3. Unfold to 2D Planar Parts with specified flap size
    print(f"Unfolding 3D mesh into 2D planar papercraft parts (Flap size: {flap_size_mm:.1f} mm, Scale: {scale:.1f}x, Join edges: {join_faces})...")
    parts = unfold_model_to_parts(
        model, target_scale=scale, flap_size_mm=flap_size_mm, texture_path=texture_path,
        join_faces=join_faces, paper_format=paper_format, margin=15.0
    )
    print(f"Unfolded into {len(parts)} papercraft parts.")

    # 4. Bin Pack onto Pages
    pages = pack_parts_onto_pages(parts, page_w, page_h, margin=15.0, part_gap=5.0)
    print(f"Packed into {len(pages)} sheet(s) [Format: {paper_format}, Margins: >= 15mm, Gaps: >= 5mm, Flaps: {flap_size_mm}mm].")

    # 5. Generate All 4 Variations
    variations = [
        (1, "1_full_color_with_lines", "Full Color + Fold Lines + Edge IDs"),
        (2, "2_full_color_no_lines",   "Full Color (Clean Outlines Only)"),
        (3, "3_blank_white_with_lines", "Blank White + Fold Lines + Edge IDs"),
        (4, "4_blank_white_no_lines",   "Blank White (Clean Outlines Only)"),
    ]

    os.makedirs(outdir, exist_ok=True)
    total_pages = len(pages)

    for var_id, var_dir_name, var_title in variations:
        var_dir = os.path.join(outdir, var_dir_name)
        os.makedirs(var_dir, exist_ok=True)
        print(f"\n[Variation {var_id}] {var_title}")

        # PDF Export
        pdf_path = os.path.join(var_dir, f"papercraft_template_{var_dir_name}.pdf")
        export_pdf_pages(pages, page_w, page_h, pdf_path, variation=var_id, footer_text=footer_text)
        print(f"  -> Generated Vector PDF: {pdf_path}")

        # SVG Export
        for p_idx, page_parts in enumerate(pages):
            svg_path = os.path.join(var_dir, f"papercraft_template_page{p_idx + 1}.svg")
            export_svg_page(page_parts, p_idx + 1, total_pages, page_w, page_h, svg_path,
                            variation=var_id, footer_text=footer_text)
            print(f"  -> Generated Vector SVG (Page {p_idx + 1}): {svg_path}")

        # 300 DPI PNG Export
        render_pdf_to_300dpi_pngs(pdf_path, var_dir, f"template_{var_dir_name}")

    print("\n[SUCCESS] Universal 3D Papercraft Pipeline completed across all 4 variations!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pepakura Alternative: Universal 3D Papercraft & Plotter Pipeline"
    )
    parser.add_argument("--input", "-i", type=str, default=None,
                        help="Path to any 3D model file (.obj)")
    parser.add_argument("--scale", type=float, default=None,
                        help="Scale multiplier (default: auto-detected, 1000 for meters, 1 for mm)")
    parser.add_argument("--texture", "-t", type=str, default=None,
                        help="Path to texture image (PNG/JPG)")
    parser.add_argument("--outdir", "-o", type=str, default="exports",
                        help="Output directory for generated templates")
    parser.add_argument("--format", "-f", type=str, default="LETTER",
                        choices=["LETTER", "A4"], help="Paper format (LETTER or A4)")
    parser.add_argument("--flap-size", "-s", type=float, default=5.5,
                        help="Depth/size of glue flaps in millimeters (default: 5.5 mm)")
    parser.add_argument("--join-edges", action="store_true",
                        help="Intelligently join faces across shared edges into connected non-overlapping islands")
    parser.add_argument("--footer", type=str, default=None,
                        help="Optional centered bold 14pt footer text inside the bottom 15mm margin")

    args = parser.parse_args()
    run_pipeline(
        input_model_path=args.input,
        texture_path=args.texture,
        outdir=args.outdir,
        paper_format=args.format,
        flap_size_mm=args.flap_size,
        user_scale=args.scale,
        join_faces=args.join_edges,
        footer_text=args.footer
    )

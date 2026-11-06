"""Tkinter 3D STEP/STP/STL preview with isolated OCC meshing and PIL rendering.

This is a shaded *preview* of triangulated CAD faces, not a dimension inspection
or a topology-preserving CAD editor.
"""
import hashlib
import gzip
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageDraw, ImageTk

# Painter rendering cannot reliably draw a randomly sampled triangle mesh:
# omitted faces produce apparent holes in CATIA-origin CAD geometry.
# Keep complete triangles and limit CAD tessellation at conversion instead.
MESH_LIMIT = 75000
DEFAULT_RGB = (187, 195, 211)
# Original light neutral background, before the dark-purple rendering experiment.
VIEWER_BACKGROUND = "#F5F8FC"
RECORD = struct.Struct("<9f3BI")
SIGNATURE = b"DRMESH2\0"


def read_stl(path):
    raw = Path(path).read_bytes()
    triangles = []
    if len(raw) >= 84 and 84 + struct.unpack_from('<I', raw, 80)[0] * 50 == len(raw):
        for index in range(struct.unpack_from('<I', raw, 80)[0]):
            values = struct.unpack_from('<12fH', raw, 84 + 50 * index)
            triangles.append((values[3:6], values[6:9], values[9:12]))
    else:
        vertices = []
        for line in raw.decode('utf-8', errors='replace').splitlines():
            parts = line.strip().split()
            if len(parts) == 4 and parts[0].lower() == 'vertex':
                try:
                    vertices.append(tuple(float(value) for value in parts[1:]))
                    if len(vertices) == 3:
                        triangles.append(tuple(vertices))
                        vertices = []
                except ValueError:
                    vertices = []
    if not triangles:
        raise ValueError('STL의 삼각형 데이터가 없습니다. 원본 파일을 확인하세요.')
    return triangles



def read_colored_mesh(path):
    triangles, colors, face_ids = [], [], []
    with gzip.open(path, 'rb') as f:
        if f.read(len(SIGNATURE)) != SIGNATURE:
            raise ValueError('색상 메시 형식이 올바르지 않습니다.')
        f.read(4)
        payload = f.read()
    if len(payload) < 8 or (len(payload)-8) % RECORD.size:
        raise ValueError('색상 메시 데이터가 손상되었습니다.')
    count, colored_count = struct.unpack_from('<II', payload, len(payload)-8)
    if count != (len(payload)-8)//RECORD.size:
        raise ValueError('색상 메시 삼각형 수가 일치하지 않습니다.')
    for values in struct.iter_unpack(RECORD.format, payload[:-8]):
        tri = (tuple(values[0:3]), tuple(values[3:6]), tuple(values[6:9]))
        triangles.append(tri)
        colors.append(tuple(values[9:12]))
        face_ids.append(int(values[12]))
    return triangles, colors, colored_count, face_ids


def mesh_cache_path(path, detail):
    st = path.stat()
    identity = f'{path.resolve()}::{st.st_size}::{st.st_mtime_ns}::{detail}::cadfaces-shading-v2'
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
    home = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    directory = Path(home) / 'DrawingReview' / 'mesh_cache'
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f'{digest}.drmesh.gz'


def read_step(path, detail='standard'):
    cache = mesh_cache_path(Path(path), detail)
    if not cache.is_file() or cache.stat().st_size < 24:
        output = cache.with_name(cache.stem + '_working.drmesh.gz')
        command = [sys.executable, str(Path(__file__).with_name('stp_mesh.py')),
                   '--input', str(path), '--output', str(output), '--detail', detail]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=180,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('STEP 메시 생성 제한 시간(3분)을 초과했습니다. 정밀도를 낮추거나 CAD 모델을 단순화하세요.') from exc
        if done.returncode != 0:
            output.unlink(missing_ok=True)
            error = (done.stderr or done.stdout or '원인을 확인할 수 없습니다.').strip()
            if 'gmsh' in error.lower() or 'dll' in error.lower():
                error += '\nSetup.cmd와 Check_3D.cmd를 실행하여 설치를 확인하세요.'
            raise RuntimeError(error[-1000:])
        output.replace(cache)
    return (*read_colored_mesh(cache), cache)


def normalize_triangles(triangles):
    xyz_min = [math.inf] * 3
    xyz_max = [-math.inf] * 3
    for tri in triangles:
        for point in tri:
            for axis in range(3):
                xyz_min[axis] = min(xyz_min[axis], point[axis])
                xyz_max[axis] = max(xyz_max[axis], point[axis])
    dimensions = [xyz_max[i] - xyz_min[i] for i in range(3)]
    radius = max(dimensions)
    if not math.isfinite(radius) or radius <= 1e-12:
        raise ValueError('표시 가능한 형상 치수가 없습니다.')
    center = [(xyz_min[i] + xyz_max[i]) / 2 for i in range(3)]
    if len(triangles) > MESH_LIMIT:
        raise ValueError(
            f'형상 삼각형이 {len(triangles):,}개로 현재 뷰어의 제한({MESH_LIMIT:,}개)을 초과합니다. '
            '면을 건너뛰어 형상을 훼손하지 않고 표시를 중단했습니다. '
            'STEP/STP 파일이라면 메시 품질을 ‘표준’으로 낮춰 다시 불러오세요.'
        )
    normalized = [tuple(tuple((pt[i] - center[i]) / radius for i in range(3)) for pt in tri)
                  for tri in triangles]
    return normalized, len(triangles), dimensions


def triangle_normal(tri):
    a, b, c = tri
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    length = math.sqrt(sum(t*t for t in n))
    return tuple(t/length for t in n) if length > 1e-15 else (0., 0., 0.)


def feature_edges(triangles, face_ids=None, crease_angle=27):
    """Preserve genuine B-Rep face boundaries, holes, chamfers and sharp edges.

    Adjacent triangles within a smooth STEP surface do NOT produce a wireframe.
    Gmsh CAD face IDs provide real topology edges even for tangent fillet seams.
    """
    edge_faces = {}
    normals = [triangle_normal(tri) for tri in triangles]
    for index, tri in enumerate(triangles):
        # STL is a triangle soup. Quantize vertices to stitch shared edges.
        keys = [tuple(round(coord, 5) for coord in pt) for pt in tri]
        for i, j in ((0, 1), (1, 2), (2, 0)):
            k = tuple(sorted((keys[i], keys[j])))
            entry = edge_faces.setdefault(k, [])
            if len(entry) < 3:
                entry.append(index)
    limit = math.cos(math.radians(crease_angle))
    # Store the adjacent triangle indices. Rendering edges in the same
    # depth-ordered pass as their surfaces allows closer CAD faces to hide
    # the edges behind them (unlike drawing all edges at the very end).
    output = []
    for (a, b), faces in edge_faces.items():
        if len(faces) == 1 or len(faces) > 2:
            output.append((a, b, tuple(faces)))
        elif len(faces) == 2:
            n1, n2 = normals[faces[0]], normals[faces[1]]
            # CAD face seams are meaningful even when two faces meet tangentially.
            from_distinct_cad_faces = (face_ids is not None and
                                       face_ids[faces[0]] != face_ids[faces[1]])
            if from_distinct_cad_faces or abs(sum(n1[i]*n2[i] for i in range(3))) < limit:
                output.append((a, b, tuple(faces)))
    return output


class ShapeViewer(ttk.Frame):
    def __init__(self, parent, title, accent='#40658E'):
        super().__init__(parent)
        self.title, self.accent = title, accent
        self.path = None
        self.triangles = []
        self.edges = []
        self.colors = []
        self.face_ids = []
        self.source_color_count = 0
        self.rotation = [0.54, -0.72]
        self.zoom = 1.0
        self.pan = [0, 0]
        self.drag = None
        self.drag_button = None
        self._request = 0
        self._image = None
        self._draw_job = None
        self.message = 'STEP / STP / STL 파일을 선택하세요.'

        bar = ttk.Frame(self)
        bar.pack(fill='x', pady=(0, 3))
        ttk.Label(bar, text=title, foreground=accent,
                  font=('Malgun Gothic', 9, 'bold')).pack(side='left')
        self.pick_button = ttk.Button(bar, text='파일 선택', command=self.choose, width=9)
        self.pick_button.pack(side='right')
        ttk.Button(bar, text='초기화', command=self.reset, width=6).pack(side='right', padx=3)

        controls = ttk.Frame(self)
        controls.pack(fill='x', pady=(0, 2))
        self.filename = ttk.Label(controls, text='선택된 3D 형상이 없습니다.', foreground='#60748A')
        self.filename.pack(side='left', fill='x', expand=True)
        self.detail = tk.StringVar(value='정밀')
        self.detail_selector = ttk.Combobox(controls, textvariable=self.detail, state='readonly',
                                            values=('표준', '정밀', '고정밀'), width=6)
        self.detail_selector.pack(side='right')
        self.detail_selector.bind('<<ComboboxSelected>>', self.change_detail)
        # CATIA-style Shading with Edges is always active; no on/off option.
        self.canvas = tk.Canvas(self, bg=VIEWER_BACKGROUND, highlightthickness=1,
                                highlightbackground='#D4DDE6', height=160)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.request_draw())
        self.canvas.bind('<ButtonPress-1>', lambda e: self.start_drag(e, 'rotate'))
        self.canvas.bind('<B1-Motion>', self.move_drag)
        self.canvas.bind('<ButtonPress-3>', lambda e: self.start_drag(e, 'pan'))
        self.canvas.bind('<B3-Motion>', self.move_drag)
        self.canvas.bind('<ButtonRelease-1>', self.stop_drag)
        self.canvas.bind('<ButtonRelease-3>', self.stop_drag)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Button-4>', lambda e: self.scale(1.12))
        self.canvas.bind('<Button-5>', lambda e: self.scale(1 / 1.12))
        ttk.Label(self, text='좌 드래그: 회전   우 드래그: 이동   휠: 확대/축소',
                  font=('Malgun Gothic', 8), foreground='#6A7D8F').pack(anchor='w')
        self.request_draw()

    def choose(self):
        filename = filedialog.askopenfilename(parent=self.winfo_toplevel(), title=self.title,
            filetypes=[('3D 형상 (STEP/STP/STL)', '*.step *.stp *.stl'),
                       ('STEP', '*.step *.stp'), ('STL', '*.stl'), ('모든 파일', '*.*')])
        if filename:
            self.load(filename)
            callback = getattr(self, 'on_file_selected', None)
            if callable(callback):
                callback(filename)

    def clear(self, message=None):
        """Clear model safely when switching exercises; cancel obsolete worker results."""
        self._request += 1
        self.path = None
        self.triangles = []
        self.edges = []
        self.colors = []
        self.face_ids = []
        self.source_color_count = 0
        self.message = message or 'STEP / STP / STL 파일을 선택하세요.'
        self.filename.configure(text='수정 STP 미선택')
        self.pick_button.configure(state='normal')
        self.request_draw()

    def change_detail(self, _event=None):
        if self.path and self.path.suffix.lower() in {'.step', '.stp'}:
            self.load(self.path)

    def load(self, path):
        path = Path(path)
        self._request += 1
        request_id = self._request
        self.path = path
        self.triangles = []
        self.edges = []
        self.colors = []
        self.face_ids = []
        self.message = 'CAD 형상을 읽어 메시를 만드는 중입니다...'
        self.filename.configure(text=path.name)
        self.pick_button.configure(state='disabled')
        self.request_draw()
        detail = {'표준': 'standard', '정밀': 'precise', '고정밀': 'ultra'}[self.detail.get()]

        def worker():
            try:
                if not path.is_file():
                    raise FileNotFoundError(f'파일이 없습니다: {path}')
                suffix = path.suffix.lower()
                if suffix == '.stl':
                    triangles, meshpath = read_stl(path), None
                    colors, source_color_count = [DEFAULT_RGB] * len(triangles), 0
                    face_ids = None
                elif suffix in {'.stp', '.step'}:
                    triangles, colors, source_color_count, face_ids, meshpath = read_step(path, detail)
                else:
                    raise ValueError('STEP, STP, STL 형식만 지원합니다.')
                normalized, triangle_count, dimensions = normalize_triangles(triangles)
                edges = feature_edges(normalized, face_ids=face_ids)
                outcome = (normalized, triangle_count, dimensions, edges, colors, source_color_count, face_ids, None)
            except Exception as error:
                outcome = (None, None, None, None, None, 0, None, str(error))
            try:
                self.after(0, self.finish_load, request_id, outcome)
            except RuntimeError:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def finish_load(self, request_id, outcome):
        if request_id != self._request or not self.winfo_exists():
            return
        vertices, triangle_count, dimensions, edges, colors, source_color_count, face_ids, error = outcome
        self.pick_button.configure(state='normal')
        if error:
            self.message = '3D 형상을 열지 못했습니다.\n' + error
            self.filename.configure(text='불러오기 실패 · ' + self.path.name)
            self.request_draw()
            return
        self.triangles = vertices
        self.edges = edges
        self.colors = colors
        self.source_color_count = source_color_count
        self.face_ids = face_ids or []
        self.message = ''
        color_status = '원본 색상 적용' if source_color_count else '색상 미포함 · 기본색'
        self.filename.configure(text=f'{self.path.name} · {triangle_count:,}면 · {color_status}')
        self.reset()

    def reset(self):
        self.rotation = [0.54, -0.72]
        self.zoom = 1.0
        self.pan = [0, 0]
        self.request_draw()

    def start_drag(self, event, mode):
        self.drag, self.drag_button = (event.x, event.y), mode

    def stop_drag(self, _event):
        self.drag = self.drag_button = None

    def move_drag(self, event):
        if not self.drag:
            return
        dx, dy = event.x - self.drag[0], event.y - self.drag[1]
        self.drag = (event.x, event.y)
        if self.drag_button == 'pan':
            self.pan[0] += dx
            self.pan[1] += dy
        else:
            self.rotation[1] += dx * 0.012
            self.rotation[0] += dy * 0.012
        self.request_draw()

    def wheel(self, event):
        self.scale(1.12 if event.delta > 0 else 1 / 1.12)
        return 'break'

    def scale(self, factor):
        self.zoom = max(.15, min(12, self.zoom * factor))
        self.request_draw()
        return 'break'

    def request_draw(self):
        if self._draw_job is not None:
            try:
                self.after_cancel(self._draw_job)
            except tk.TclError:
                pass
        self._draw_job = self.after(30, self.draw)

    def draw(self):
        """Shading with Edges: two-light CAD shading with B-Rep feature seams.

        Render each complete triangle by depth; outline only CAD/severe angle
        boundaries. Supersampling avoids jagged hole and chamfer contours.
        """
        self._draw_job = None
        if not self.winfo_exists():
            return
        canvas = self.canvas
        canvas.delete('all')
        w, h = max(canvas.winfo_width(), 130), max(canvas.winfo_height(), 90)
        if not self.triangles:
            canvas.create_text(w / 2, h / 2, text=self.message, fill='#64748B',
                width=max(w - 20, 100), justify='center', font=('Malgun Gothic', 9))
            return

        # A moderate supersampling factor keeps circular bores rounded without
        # requiring denser meshes just for anti-aliasing.
        ss = 2 if w * h < 700_000 and len(self.triangles) <= 18000 else 1
        rw, rh = w * ss, h * ss
        ax, ay = self.rotation
        sx, cx, sy, cy = math.sin(ax), math.cos(ax), math.sin(ay), math.cos(ay)
        side = min(rw, rh) * self.zoom * .76
        center_x, center_y = rw / 2 + self.pan[0] * ss, rh / 2 + self.pan[1] * ss

        def camera(pt):
            x, y, z = pt
            rx, rz = cy*x + sy*z, -sy*x + cy*z
            return (center_x + rx*side,
                    center_y - (cx*y - sx*rz)*side,
                    (sx*y + cx*rz)*side)

        # Build separately projected crease/boundary edges and associate them
        # with the surface that owns them. This suppresses obvious back wires.
        edges_by_face = {}
        for va, vb, face_ids in self.edges:
            a, b = camera(va), camera(vb)
            segment = ((a[0], a[1]), (b[0], b[1]))
            for face_id in face_ids:
                edges_by_face.setdefault(face_id, []).append(segment)

        # Use camera-space lighting. Ambient fill protects STEP source colors,
        # key light separates planes, side light exposes chamfer walls and holes.
        key = (-.52, .71, .47)
        fill_light = (.64, -.12, .76)
        polygons = []
        for tri_id, (tri, base_color) in enumerate(zip(self.triangles, self.colors)):
            projected = tuple(camera(pt) for pt in tri)
            a, b, c = projected
            ux, uy, uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
            vx, vy, vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
            nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
            norm = math.sqrt(nx*nx+ny*ny+nz*nz)
            if norm < 1e-8:
                continue
            nx, ny, nz = nx/norm, ny/norm, nz/norm
            # Two-sided lighting also works for STL exports with flipped normals.
            main = abs(key[0]*nx + key[1]*ny + key[2]*nz)
            secondary = abs(fill_light[0]*nx + fill_light[1]*ny + fill_light[2]*nz)
            intensity = .51 + .34 * main + .15 * secondary
            # An additional view-alignment cue separates top planes from holes.
            intensity *= (.87 + .13*abs(nz))
            fill_rgb = tuple(max(0, min(255, round(channel * intensity))) for channel in base_color)
            polygons.append(((a[2]+b[2]+c[2])/3,
                            ((a[0],a[1]), (b[0],b[1]), (c[0],c[1])),
                            fill_rgb, tri_id))

        picture = Image.new('RGB', (rw, rh), VIEWER_BACKGROUND)
        brush = ImageDraw.Draw(picture)
        # Back-to-front face drawing gives robust visual results for many common
        # solid parts; this is a lightweight preview rather than a GPU z-buffer.
        for _depth, points, color, face_id in sorted(polygons, key=lambda item: item[0]):
            brush.polygon(points, fill=color)
            for segment in edges_by_face.get(face_id, ()):
                brush.line(segment, fill='#151820', width=max(1, ss))
        if ss > 1:
            picture = picture.resize((w, h), Image.Resampling.LANCZOS)
        self._image = ImageTk.PhotoImage(picture, master=canvas)
        canvas.create_image(0, 0, anchor='nw', image=self._image)

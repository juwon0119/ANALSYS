"""Generate a colored display mesh from a STEP/STP B-Rep using Gmsh OCC.

Per-face STEP colors are transferred to per-triangle records in a gzip file;
no source CAD geometry is modified. Fully transparent Gmsh entity colors mean
that the source face has no explicit STEP color, NOT that it is blue.
"""
import argparse
import gzip
import struct
import sys
from pathlib import Path

SIGNATURE = b'DRMESH2\0'
RECORD = struct.Struct('<9f3BI')
DEFAULT_RGB = (187, 195, 211)


def check():
    try:
        import gmsh
        print('Gmsh Python API: OK', getattr(gmsh, '__version__', ''))
        gmsh.initialize(['-nopopup'])
        gmsh.finalize()
        print('OpenCASCADE / Gmsh initialization: OK')
        return 0
    except Exception as exc:
        print(f'Gmsh 초기화 실패: {exc}', file=sys.stderr)
        print('Setup.cmd를 실행하거나 Windows 64비트 Python 및 필요한 DLL을 확인하세요.', file=sys.stderr)
        return 1


def face_color(gmsh, face_tag, volume_colors):
    r, g, b, a = gmsh.model.getColor(2, face_tag)
    if a:
        return (r, g, b), True
    for color in volume_colors.get(face_tag, []):
        if color[3]:
            return color[:3], True
    return DEFAULT_RGB, False


def convert(source: Path, target: Path, detail: str):
    import gmsh
    source = source.resolve(strict=True)
    if source.suffix.lower() not in {'.stp', '.step'}:
        raise ValueError('STEP/STP 파일만 변환할 수 있습니다.')
    target.parent.mkdir(parents=True, exist_ok=True)
    gmsh.initialize(['-nopopup'])
    try:
        gmsh.option.setNumber('General.Terminal', 0)
        gmsh.option.setNumber('General.Verbosity', 1)
        gmsh.model.add('CATIA_STEP_preview')
        imported = gmsh.model.occ.importShapes(str(source))
        if not imported:
            raise ValueError('파일에서 읽을 수 있는 CAD 형상을 찾지 못했습니다.')
        gmsh.model.occ.synchronize()
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        diagonal = ((xmax-xmin)**2+(ymax-ymin)**2+(zmax-zmin)**2)**.5
        if diagonal <= 1e-10:
            raise ValueError('형상의 크기가 0입니다.')
        ratio = {'standard': .052, 'precise': .025, 'ultra': .012}[detail]
        gmsh.option.setNumber('Mesh.MeshSizeMin', max(diagonal*ratio/5, 1e-8))
        gmsh.option.setNumber('Mesh.MeshSizeMax', diagonal*ratio)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature', {'standard':12,'precise':24,'ultra':40}[detail])
        gmsh.option.setNumber('Mesh.MeshSizeFromPoints', 1)
        gmsh.option.setNumber('Mesh.MeshSizeExtendFromBoundary', 1)
        gmsh.option.setNumber('Mesh.Algorithm', 6)
        gmsh.model.mesh.generate(2)
        # gmsh.step boundary faces may carry color while solids do not.
        # Also inherit solid color when a face has no explicit color.
        inherited = {}
        for dim, volume in gmsh.model.getEntities(3):
            c = gmsh.model.getColor(dim, volume)
            if c[3]:
                boundaries = gmsh.model.getBoundary([(dim,volume)], oriented=False)
                for d, face in boundaries:
                    if d == 2:
                        inherited.setdefault(face, []).append(c)
        node_tags, xyz, _ = gmsh.model.mesh.getNodes()
        points = {int(t): tuple(float(v) for v in xyz[i*3:i*3+3]) for i,t in enumerate(node_tags)}
        count = colored = 0
        with gzip.open(target, 'wb', compresslevel=4) as out:
            out.write(SIGNATURE)
            out.write(struct.pack('<I', 0))  # count placed in trailer, stream friendly
            for dim, face in gmsh.model.getEntities(2):
                color, explicit = face_color(gmsh, face, inherited)
                types, _, connectivities = gmsh.model.mesh.getElements(2, face)
                for kind, connectivity in zip(types, connectivities):
                    # Only 3-node linear triangles can be displayed by this viewer.
                    _, _, _, nodes_per_element, _, _ = gmsh.model.mesh.getElementProperties(int(kind))
                    if nodes_per_element != 3:
                        continue
                    for start in range(0, len(connectivity), 3):
                        triangle = (points[int(tag)] for tag in connectivity[start:start+3])
                        flat = [coordinate for vertex in triangle for coordinate in vertex]
                        out.write(RECORD.pack(*flat, *color, int(face)))
                        count += 1
                        colored += int(explicit)
            out.write(struct.pack('<II', count, colored))
        if not count:
            target.unlink(missing_ok=True)
            raise ValueError('CAD 표면 삼각형을 생성하지 못했습니다.')
        print(f'OK: {count} triangles, source-colored: {colored}; {target.name}')
    finally:
        gmsh.finalize()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--detail', choices=('standard','precise','ultra'), default='precise')
    args = parser.parse_args()
    if args.check:
        return check()
    if not args.input or not args.output:
        parser.error('--input과 --output을 모두 지정하세요.')
    try:
        convert(args.input,args.output,args.detail)
        return 0
    except Exception as error:
        print(f'변환 실패: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

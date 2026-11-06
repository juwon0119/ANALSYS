"""Regression tests for relative shape comparison. Run via Check_Geometry.cmd."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import math
import tempfile
import ezdxf
from feature_geometry_compare import compare_native_geometry, compare_closed_outline_geometry
from dxf_pair_review import compare_dxf


def circle_doc(offset=(0,0),scale=1,rotation=0,alter=0):
    doc=ezdxf.new();m=doc.modelspace()
    co=math.cos(rotation);si=math.sin(rotation)
    for i,(x,y) in enumerate([(0,0),(20,0),(3,13),(27,23)]):
        x,y=offset[0]+scale*(x*co-y*si),offset[1]+scale*(x*si+y*co)
        m.add_circle((x,y),(3+(alter if i==2 else 0))*scale)
    return doc


def polygon_doc(h,scale=1,offset=(0,0)):
    doc=ezdxf.new();m=doc.modelspace()
    points=[(0,0),(25,0),(25,h),(0,h)]
    points=[(x*scale+offset[0],y*scale+offset[1]) for x,y in points]
    for a,b in zip(points,points[1:]+points[:1]):m.add_line(a,b)
    return doc


def test():
    a=circle_doc()
    assert not compare_native_geometry(a,circle_doc((120,70),2,.8)), 'rigid transformed circle configuration false positive'
    assert any('반경' in v.title for v in compare_native_geometry(a,circle_doc((120,70),2,.8,1))), 'relative radius change missed'
    assert not compare_closed_outline_geometry(polygon_doc(15),polygon_doc(15,2,(90,60)))
    assert any('외곽' in v.title for v in compare_closed_outline_geometry(polygon_doc(15),polygon_doc(18,2,(90,60))))
    with tempfile.TemporaryDirectory() as temp:
        a_path=Path(temp)/'reference.dxf';b_path=Path(temp)/'check.dxf'
        a.saveas(a_path)
        circle_doc((120,70),2,.8,1).saveas(b_path)
        findings=compare_dxf(a_path,b_path,selected={'shape'})
        assert any('반경' in v.title for v in findings)
        # No text differences may be automatically invented.
        assert all(v.kind=='shape' for v in findings)
    print('PASS: position/scale/rotation invariance, circle radius change, closed contour change, DXF integration')

if __name__=='__main__':test()

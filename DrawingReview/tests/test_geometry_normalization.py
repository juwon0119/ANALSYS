"""Run: python -m unittest discover -s tests -p 'test_*.py'."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
from feature_geometry_compare import _rings, _closed_outlines, compare_native_geometry
from difference_review import Finding
from dxf_pair_review import compare_dxf


class GeometryNormalizationTests(unittest.TestCase):
    def drawing(self, changed=False):
        doc = ezdxf.new()
        block = doc.blocks.new('holes')
        for i, (x, y) in enumerate(((0, 0), (20, 0), (3, 13), (27, 23))):
            block.add_circle((x, y), 4 if changed and i == 2 else 3)
        outer = doc.blocks.new('part')
        outer.add_blockref('holes', (0, 0))
        doc.modelspace().add_blockref('part', (100, 70),
                                     dxfattribs={'xscale': 2, 'yscale': 2, 'rotation': 35})
        return doc

    def test_nested_block_radius_change(self):
        a, b = self.drawing(), self.drawing(True)
        self.assertEqual(len(_rings(a)), 4)
        self.assertAlmostEqual(_rings(a)[0].r, 6)
        self.assertTrue(any('반경' in f.title for f in compare_native_geometry(a, b)))
        self.assertFalse(compare_native_geometry(a, self.drawing()))

    def test_curved_polyline_is_not_straight_polygon(self):
        doc = ezdxf.new()
        doc.modelspace().add_lwpolyline([(0, 0, 1), (20, 0, 0), (20, 10, 0)],
                                       format='xyb', close=True)
        self.assertEqual(_closed_outlines(doc), [])

    def test_dimension_selection_is_independent_of_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.dxf', Path(directory)/'b.dxf'
            ezdxf.new().saveas(a)
            ezdxf.new().saveas(b)
            candidates = [Finding(0, 'dimension', '확인 필요', '寸法', 'test'),
                          Finding(0, 'shape', '확인 필요', 'shape', 'test')]
            with patch('feature_geometry_compare.compare_native_geometry', return_value=candidates):
                self.assertEqual([f.kind for f in compare_dxf(a,b,selected={'dimension'})], ['dimension'])
                self.assertEqual([f.kind for f in compare_dxf(a,b,selected={'shape'})], ['shape'])


if __name__ == '__main__':
    unittest.main()

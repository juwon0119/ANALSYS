"""Export a model-space preview for drawing review."""
from pathlib import Path

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration


def export_pdf(source: Path, destination: Path):
    if source.suffix.lower() != ".dxf" or destination.suffix.lower() != ".pdf":
        raise ValueError("DXF를 선택하고 새 PDF 이름으로 저장하세요.")
    content = render_dxf_pdf(source)
    with destination.open("xb") as output:
        output.write(content)


def render_dxf_pdf(source: Path, marker=None):
    drawing = ezdxf.readfile(source)
    backend = pymupdf.PyMuPdfBackend()
    config = Configuration(color_policy=ColorPolicy.BLACK,
                           background_policy=BackgroundPolicy.WHITE)
    frontend = Frontend(RenderContext(drawing), backend, config=config)
    if marker:
        config = Configuration(background_policy=BackgroundPolicy.WHITE)
        frontend = Frontend(RenderContext(drawing), backend, config=config)

        def mark(entity, properties):
            properties.color = ("#ff00ff" if entity.dxftype() == "MTEXT"
                                and entity.plain_text() == marker else "#000000")

        frontend.push_property_override_function(mark)
    frontend.draw_layout(drawing.modelspace())
    page = layout.Page(297, 210, layout.Units.mm, margins=layout.Margins.all(8))
    return backend.get_pdf_bytes(page)

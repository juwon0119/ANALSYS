"""Conservative, handle-independent recognition and correction of training depth annotations.

Never modify a dimension unless a unique modelspace DIMENSION and its own
rendering-block MTEXT agree about the depth value.
"""
from pathlib import Path
import os
import re
import tempfile

import ezdxf
import pymupdf as fitz


def _locate_depth(doc, dim_handle, label_handle, dxf_prefix, label_prefix):
    """Return unique corroborated (DIMENSION, MTEXT, depth); do not guess by location."""
    depth_re = re.compile(re.escape(label_prefix) + r"\s+(\d+(?:\.\d+)?)\s*$", re.I)
    candidates = []
    for dimension in doc.modelspace().query("DIMENSION"):
        geometry = dimension.dxf.get("geometry", "")
        if not geometry or geometry not in doc.blocks:
            continue
        dim_text = dimension.dxf.get("text", "")
        for label in doc.blocks[geometry].query("MTEXT"):
            match = depth_re.fullmatch(label.text.strip())
            if not match:
                continue
            value = match.group(1)
            old = f"{dxf_prefix} {value}\\P"
            if dim_text.count(old) == 1:
                candidates.append((dimension, label, value))
    # Only target a single *verified* annotation; never trust a handle alone.
    if len(candidates) != 1:
        raise ValueError(
            f"대상 깊이 치수를 하나로 특정할 수 없습니다 (확인된 후보 {len(candidates)}건). "
            "DXF 치수 문구와 치수 블록의 표시 문자를 확인하세요. "
            "다른 항목의 종합검토는 계속할 수 있습니다."
        )
    return candidates[0]


def review_depth(pdf, source, output, approve, report=print, *, pdf_prefix,
                 dxf_prefix, label_prefix, dim_handle, label_handle):
    with fitz.open(pdf) as reference:
        extracted = reference[0].get_text()
        hits = re.findall(re.escape(pdf_prefix).replace(r"\ ", r"\s+") + r"\s+(\d+(?:\.\d+)?)",
                          extracted)
    if len(hits) != 1:
        raise ValueError("기준 PDF 첫 페이지에서 깊이 표기를 하나로 특정할 수 없습니다.")
    target = hits[0]
    original = source.read_bytes()
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "source.dxf"
        snapshot.write_bytes(original)
        doc = ezdxf.readfile(snapshot)
        dimension, label, current = _locate_depth(doc, dim_handle, label_handle,
                                                  dxf_prefix, label_prefix)
        actual_dim_handle, actual_label_handle = dimension.dxf.handle, label.dxf.handle
        if current == target:
            report(f"기준 PDF와 검토 DXF 일치: DP {target}")
            return
        report(f"PDF 1쪽 근거: {pdf_prefix} {target}")
        report(f"수정 제안: {current} → {target} (DIMENSION {actual_dim_handle}, MTEXT {actual_label_handle})")
        destination = output(current, target) if callable(output) else output
        if destination is None or not approve():
            return
        if source.read_bytes() != original:
            raise ValueError("승인 중 검토 파일이 변경되었습니다. 다시 분석하세요.")
        old = f"{dxf_prefix} {current}\\P"
        new = f"{dxf_prefix} {target}\\P"
        dimension.dxf.text = dimension.dxf.text.replace(old, new)
        label.text = f"{label_prefix} {target}"
        if destination.suffix.lower() != ".dxf":
            raise ValueError("출력 파일 확장자는 .dxf여야 합니다.")
        with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
            staged = Path(staging) / "corrected.dxf"
            doc.saveas(staged)
            checked = ezdxf.readfile(staged)
            if (checked.entitydb[actual_dim_handle].dxf.text != dimension.dxf.text
                    or checked.entitydb[actual_label_handle].text != label.text):
                raise ValueError("저장 후 검증 실패")
            os.link(staged, destination)
        report(f"두 객체의 저장값 확인 완료: {destination}")

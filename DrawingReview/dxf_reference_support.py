"""Internal DXF rendering only for legacy, uniquely verified depth correction.
No PDF is ever requested as reference input or saved to a user's drawing folder.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from export_pdf import render_dxf_pdf

@contextmanager
def rendered_reference_pdf(reference_dxf):
    path=Path(reference_dxf)
    if path.suffix.lower()!='.dxf' or not path.is_file():
        raise ValueError('실제 기준 DXF 파일을 먼저 선택하세요.')
    with tempfile.TemporaryDirectory() as folder:
        target=Path(folder)/'reference_rendered.pdf'
        target.write_bytes(render_dxf_pdf(path))
        yield target


def review_dxf_depth(reference, source, output, approve, report, *, prefix):
    """Approve one unique, native-DXF depth edit only when both files agree on callout identity."""
    import re
    import tempfile
    import os
    import ezdxf
    from review_depth import _locate_depth
    normalized=prefix.replace('C/BØ','C/B %%c')
    label_prefix=prefix.replace('C/BØ','').strip()
    # The label text is in the native rendered dimension block and must match uniquely.
    ref_doc=ezdxf.readfile(reference)
    src_doc=ezdxf.readfile(source)
    ref_dim,ref_label,target=_locate_depth(ref_doc,None,None,normalized,label_prefix)
    dim,label,current=_locate_depth(src_doc,None,None,normalized,label_prefix)
    if target==current:
        report(f'기준 DXF와 검토 DXF 일치: DP {target}')
        return
    report(f'기준 DXF 근거: {prefix} {target}')
    report(f'수정 제안: {current} → {target} (DIMENSION {dim.dxf.handle}, MTEXT {label.dxf.handle})')
    candidate=output(current,target) if callable(output) else output
    if candidate is None or not approve():
        return
    candidate=Path(candidate)
    if candidate.suffix.lower()!='.dxf' or candidate.exists():
        raise ValueError('수정본은 새 DXF 파일 경로로 저장해야 합니다.')
    old=f'{normalized} {current}\\P'
    new=f'{normalized} {target}\\P'
    if dim.dxf.text.count(old)!=1:
        raise ValueError('검토 DXF 수정 문자열을 유일하게 특정하지 못했습니다.')
    dim.dxf.text=dim.dxf.text.replace(old,new)
    label.text=f'{label_prefix} {target}'
    with tempfile.TemporaryDirectory(dir=candidate.parent) as folder:
        staged=Path(folder)/'result.dxf'
        src_doc.saveas(staged)
        checked=ezdxf.readfile(staged)
        if checked.entitydb[dim.dxf.handle].dxf.text!=dim.dxf.text:
            raise ValueError('수정본 저장 후 검증 실패')
        os.link(staged,candidate)
    report(f'두 객체의 저장값 확인 완료: {candidate}')

"""DXF-to-DXF evidence based comparison. No PDF source required.

The renderer is used only to place *verified* native text on the comparison UI.
Dimensions, geometry and layer information are read from native DXF entities.
No proposed edit is authorized by this general comparison.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import math
import re
import ezdxf
import pymupdf
from difference_review import Finding, CATEGORY, _plain
from export_pdf import render_dxf_pdf


def _norm(s):
    return re.sub(r'\s+', ' ', _plain(s)).strip().upper()


def _kind(s):
    upper=_norm(s).replace('⌀','Ø').replace('Φ','Ø')
    if any(x in upper for x in ('±', '+0.', '-0.', '\\S')):
        return 'tolerance'
    if re.search(r'(?:Ø|%%C)\s*\d',upper):
        return 'diameter'
    if re.search(r'(?:\bDP\s*\d|\bR\s*\d|\bC\s*\d|\d+\.\d+|\b\d+\b)',upper):
        return 'dimension'
    return 'text'


def _native_text(doc):
    """Only top-level text plus dimension text and block render labels.

    Renderer block texts are not assumed to have valid model-space coordinates.
    """
    entries=[]
    for ent in doc.modelspace():
        typ=ent.dxftype()
        if typ in ('TEXT','MTEXT'):
            value=ent.dxf.text if typ=='TEXT' else ent.text
            if _norm(value):
                entries.append((str(value),str(ent.dxf.handle)))
        elif typ=='DIMENSION':
            override=str(ent.dxf.get('text','') or '')
            if override and override != '<>':
                entries.append((override,str(ent.dxf.handle)))
            # Rendered DIMENSION blocks contain nominal values and tolerances
            # that are not exposed as separate modelspace entities.
            block=ent.dxf.get('geometry','')
            if block and block in doc.blocks:
                for child in doc.blocks[block]:
                    if child.dxftype() in ('TEXT','MTEXT'):
                        value=child.dxf.text if child.dxftype()=='TEXT' else child.text
                        if _norm(value):
                            entries.append((str(value),str(ent.dxf.handle)))
    return entries


def _unique_boxes(path, values):
    """Highlight native MTEXT in the *same* DXF renderer as the viewer.

    This resolves plotted positions even when the PDF export has no selectable
    text (common with outlined SHX fonts). Never guess screen coordinates.
    """
    from drawing_preview import render_image
    found={}
    # Rendering every annotation would make large drawings unresponsive.
    for value in list(dict.fromkeys(values))[:18]:
        text=_plain(value).strip()
        if not text or len(text)>90:
            continue
        try:
            rendered,region=render_image(Path(path),True,text)
        except Exception:
            continue
        if region:
            w,h=rendered.size
            found[value]=tuple(max(0.,min(1.,float(n)/scale)) for n,scale in zip(region,(w,h,w,h)))
    return found


def _geometry_counts(doc):
    types=('LINE','ARC','CIRCLE','LWPOLYLINE','POLYLINE','ELLIPSE','SPLINE')
    counts=Counter(e.dxftype() for e in doc.modelspace() if e.dxftype() in types)
    center=Counter((e.dxftype(), str(e.dxf.get('layer','')).upper(), str(e.dxf.get('linetype','')).upper())
                   for e in doc.modelspace() if ('CENTER' in str(e.dxf.get('layer','')).upper() or
                    'CENTER' in str(e.dxf.get('linetype','')).upper()))
    return counts, center


def _annotation_diff(reference_path, compared_path, selected):
    left=ezdxf.readfile(reference_path);right=ezdxf.readfile(compared_path)
    a=_native_text(left);b=_native_text(right)
    aa=Counter(_norm(t) for t,_ in a)
    bb=Counter(_norm(t) for t,_ in b)
    only_a=aa-bb;only_b=bb-aa
    # Do not pair unrelated labels by order. Pair distinctive machining callouts
    # using identical nonnumeric signatures only when unique on both sides.
    def sig(s):
        return re.sub(r'\d+(?:\.\d+)?','#',s)
    used_a=set();used_b=set();pairs=[]
    for label_a in only_a:
        matching=[label_b for label_b in only_b if sig(label_a)==sig(label_b)]
        if len(matching)==1 and only_a[label_a]==only_b[matching[0]]==1:
            label_b=matching[0]
            if sum(sig(t)==sig(label_a) for t in only_a)==1:
                pairs.append((label_a,label_b));used_a.add(label_a);used_b.add(label_b)
    # Only attempt localization for actual candidates, not every drawing label.
    # The native modelspace and renderer may use distinct text representations.
    # We only trust uniquely highlighted MTEXT bounding boxes.
    values_a={t for t,_ in a};values_b={t for t,_ in b}
    # Normalize values back to one uniquely observed raw label for position.
    def unique_raw(rows):
        d={}
        for v,_ in rows:d.setdefault(_norm(v),[]).append(v)
        return {key:items[0] for key,items in d.items() if len(items)==1}
    raw_a=unique_raw(a);raw_b=unique_raw(b)
    wanted_a=[raw_a[n] for n in (list(used_a)+list(only_a)) if n in raw_a]
    wanted_b=[raw_b[n] for n in (list(used_b)+list(only_b)) if n in raw_b]
    boxes_a=_unique_boxes(reference_path,wanted_a)
    boxes_b=_unique_boxes(compared_path,wanted_b)
    # Repeated identical-form labels are never paired solely by position.
    # Orthographic views can move or rescale; ambiguous pairs remain unlinked.
    findings=[]
    for va,vb in pairs:
        cat='diameter' if ('Ø' in va or 'Ø' in vb or ('HOLE THRU' in va and 'HOLE THRU' in vb)) else ('tolerance' if any(c in va+vb for c in ('±','+0.','-0.')) else ('dimension' if any(ch.isdigit() for ch in va+vb) else 'text'))
        if cat not in selected:continue
        boxa=boxes_a.get(raw_a.get(va));boxb=boxes_b.get(raw_b.get(vb))
        findings.append(Finding(0,cat,'불일치','대응 표기값 차이',
             '양쪽 DXF의 표기 구조와 표시 위치가 대응합니다. 자동 표시 좌표는 각 DXF 렌더링의 문자 위치에서 추출했습니다.' if boxa and boxb else
             '유사 표기이지만 한쪽 이상의 정확한 표시 좌표가 불명확합니다. 위치를 수동 확인하세요.',
             va,vb,boxa,boxb))
    for vals,src in ((only_a,'reference'),(only_b,'compared')):
        for v,count in vals.items():
            if v in (used_a if src=='reference' else used_b):continue
            if count>3:continue
            cat=_kind(v)
            if cat not in selected:continue
            raw=(raw_a if src=='reference' else raw_b).get(v)
            box=(boxes_a if src=='reference' else boxes_b).get(raw)
            findings.append(Finding(0,cat,'확인 필요',
                '기준 DXF에만 존재하는 표기' if src=='reference' else '검토 DXF에만 존재하는 표기',
                '단독 표기 검출. 해당 도면 위치가 다른 표기와 대응하는지 검토가 필요합니다.',
                v if src=='reference' else '',v if src=='compared' else '',
                box if src=='reference' else None,box if src=='compared' else None))
    if selected & {'shape', 'dimension'}:
        from feature_geometry_compare import compare_native_geometry, attach_verified_dxf_positions
        native = compare_native_geometry(left,right)
        native = [f for f in native if f.kind in selected]
        findings.extend(attach_verified_dxf_positions(native,reference_path,compared_path))
    if 'shape' in selected:
        from feature_geometry_compare import compare_closed_outline_geometry
        findings.extend(compare_closed_outline_geometry(left,right))
        ca,_=_geometry_counts(left); cb,_=_geometry_counts(right)
        if ca!=cb:
            findings.append(Finding(0,'shape','확인 필요','기하 객체 구성 수량 차이 (위치 미확정)',
                '객체 수량만으로 형상 불일치를 확정하지 않습니다. 대응 요소를 직접 확인하세요.',
                str(dict(ca)),str(dict(cb))))
    return findings


def compare_dxf(reference_path, compared_path, selected=None):
    reference_path=Path(reference_path);compared_path=Path(compared_path)
    if reference_path.suffix.lower()!='.dxf' or compared_path.suffix.lower()!='.dxf':
        raise ValueError('기준 도면과 검토 도면은 모두 DXF여야 합니다.')
    if reference_path.resolve()==compared_path.resolve():
        raise ValueError('기준 DXF와 검토 DXF가 같은 파일입니다. 서로 다른 파일을 선택하세요.')
    selected=set(CATEGORY) if selected is None else set(selected)
    findings=_annotation_diff(reference_path,compared_path,selected)
    # Native CAD comparison after textual matching. These are hypotheses, not
    # training examples and not guarantees of geometric equivalence.
    if 'center' in selected or 'tolerance' in selected:
        from semantic_pair_upgrade import centerline_findings, tolerance_findings
        reference_doc=ezdxf.readfile(reference_path)
        compared_doc=ezdxf.readfile(compared_path)
        if 'center' in selected:
            try:
                findings.extend(centerline_findings(reference_path,compared_path,
                                                    reference_doc,compared_doc))
            except Exception as error:
                findings.append(Finding(0,'center','확인 필요','중심선 객체 분석 실패',
                    f'중심선 분석 중 오류: {error}. 직접 검토하세요.'))
        if 'tolerance' in selected:
            try:
                existing={(f.kind,f.reference,f.compared) for f in findings}
                for f in tolerance_findings(reference_path,compared_path,reference_doc,compared_doc):
                    if (f.kind,f.reference,f.compared) not in existing:
                        findings.append(f)
            except Exception as error:
                findings.append(Finding(0,'tolerance','확인 필요','공차 객체 분석 실패',
                    f'공차 분석 중 오류: {error}. 직접 검토하세요.'))
    for index, f in enumerate(findings,1):f.number=index
    return findings


def report_dxf(findings, reference, compared):
    lines=['[DXF ↔ DXF 종합 검토]',f'기준 DXF: {Path(reference).name}',
           f'검토 DXF: {Path(compared).name}',
           '※ 미검출은 일치 확정이 아닙니다. 분해된 치수·이름이 다른 레이어·복합 형상은 별도 확인해야 합니다.', '']
    try:
        from feature_geometry_compare import geometry_coverage
        coverage=geometry_coverage(ezdxf.readfile(reference),ezdxf.readfile(compared))
        lines.extend([
            '[형상 대응 분석 현황]',
            f"원 객체 {coverage['circles'][0]} / {coverage['circles'][1]}",
            f"원 중심 군집(3개 이상) {coverage['circle_groups'][0]} / {coverage['circle_groups'][1]}",
            f"고유하게 대응된 원 중심 군집 {coverage['unique_circle_group_pairs']}쌍",
            f"닫힌 윤곽선 {coverage['closed_contours'][0]} / {coverage['closed_contours'][1]}",
            f"DIMENSION 객체 {coverage['native_dimensions'][0]} / {coverage['native_dimensions'][1]}",
            '※ 객체 개수는 검토 근거이며 단독으로 도면 불일치를 뜻하지 않습니다.',
            '※ 3개 미만의 원 중심·대칭 배치·분해된 외곽 형상은 자동 대응을 보류할 수 있습니다.',
            ''
        ])
    except (OSError,ValueError,ezdxf.DXFError):
        lines.append('형상 대응 통계 생성 실패 · 개별 검토 결과만 확인하세요.\n')
    lines += [f.report_line().replace('PDF:', '기준 DXF:') for f in findings]
    if not findings:lines.append('비교 조건에서 검출된 후보가 없습니다. 모든 형상 일치를 보증하지 않습니다.')
    return '\n'.join(lines)

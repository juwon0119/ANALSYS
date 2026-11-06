"""Conservative native-DXF centerline and tolerance matching.

Only unambiguous native entity pairs yield automatic findings.  Drawing-review
examples are not used as hidden templates or rules for other source drawings.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math
import re
import ezdxf
from difference_review import Finding, _plain


def centerline(ent):
    layer = str(ent.dxf.get('layer', '')).upper()
    ltype = str(ent.dxf.get('linetype', '')).upper()
    if ltype in ('', 'BYLAYER', 'BYBLOCK'):
        try:
            ltype = str(ent.doc.layers.get(ent.dxf.layer).dxf.linetype).upper()
        except (AttributeError, KeyError):
            pass
    tags = ('CENTER', 'CENTRE', '중심', '일점쇄', 'CENTERLINE')
    return any(tag in layer or tag in ltype for tag in tags)


def _segment(ent):
    if ent.dxftype() != 'LINE':
        return None
    a, b = ent.dxf.start, ent.dxf.end
    dx, dy = float(b.x-a.x), float(b.y-a.y)
    length = math.hypot(dx,dy)
    if length < 0.2:
        return None
    return (float((a.x+b.x)/2), float((a.y+b.y)/2), length,
            math.atan2(dy,dx)%math.pi, str(ent.dxf.handle))


def _center_segments(doc):
    return [s for e in doc.modelspace() if centerline(e) if (s:=_segment(e))]


def _match_score(a,b):
    # Native drawing coordinates; reject if sheet coordinates are not consistent.
    distance=math.hypot(a[0]-b[0],a[1]-b[1])
    angle=abs(a[3]-b[3]);angle=min(angle,math.pi-angle)
    relative=abs(a[2]-b[2])/max(a[2],b[2])
    if angle > math.radians(6) or relative > .12:
        return None
    if distance > max(2.0,min(a[2],b[2])*.15):
        return None
    return distance/max(a[2],b[2]) + relative + angle


def _highlight_handles(path, handles):
    """Find positions by actually rendering the selected CAD entities in magenta.

    Avoid guessed model-to-screen transforms. Returned boxes are normalized
    to the very same DXF preview renderer as the comparison dialog.
    """
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    from PIL import Image
    import pymupdf as fitz
    from io import BytesIO
    if not handles:
        return {}
    doc=ezdxf.readfile(path)
    marked={h for h in handles if h in doc.entitydb}
    result={}
    for handle in list(marked)[:12]:
        selected_entity=doc.entitydb[handle]
        target_handles={handle}
        if selected_entity.dxftype()=='DIMENSION':
            geometry=selected_entity.dxf.get('geometry','')
            if geometry and geometry in doc.blocks:
                target_handles.update(str(child.dxf.handle) for child in doc.blocks[geometry]
                                      if child.dxftype() in ('MTEXT','TEXT','LINE','ARC','CIRCLE'))
        backend=pymupdf.PyMuPdfBackend()
        config=Configuration(background_policy=BackgroundPolicy.WHITE)
        frontend=Frontend(RenderContext(doc),backend,config=config)
        def override(entity,properties):
            if str(entity.dxf.get('handle')) in target_handles:
                properties.color='#ff00ff'
            else:
                properties.color='#000000'
        frontend.push_property_override_function(override)
        frontend.draw_layout(doc.modelspace())
        page=layout.Page(297,210,layout.Units.mm,margins=layout.Margins.all(8))
        pdfbytes=backend.get_pdf_bytes(page)
        with fitz.open(stream=pdfbytes,filetype='pdf') as output:
            pg=output[0]
            scale=1200/max(pg.rect.width,pg.rect.height)
            pix=pg.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            import numpy as np
            arr=np.asarray(im)
            mask=(arr[:,:,0]>155)&(arr[:,:,2]>95)&(arr[:,:,1]<120)
            yy,xx=np.where(mask)
            if len(xx)>4:
                pad=6
                result[handle]=(max(0.,(int(xx.min())-pad)/pix.width),
                    max(0.,(int(yy.min())-pad)/pix.height),
                    min(1.,(int(xx.max())+pad)/pix.width),
                    min(1.,(int(yy.max())+pad)/pix.height))
    return result


def _aligned_sheet_space(reference, compared):
    """Conservative check that model-space sheets use the same coordinates."""
    def anchors(doc):
        result=set()
        for ent in doc.modelspace().query('LINE'):
            a,b=ent.dxf.start,ent.dxf.end
            if math.hypot(a.x-b.x,a.y-b.y) < 85:continue
            key=(round(min(a.x,b.x),1),round(min(a.y,b.y),1),
                 round(max(a.x,b.x),1),round(max(a.y,b.y),1))
            result.add(key)
        return result
    a,b=anchors(reference),anchors(compared)
    return len(a & b)>=3 and len(a & b) >= .6*min(len(a),len(b))


def _expanded_center_region(box):
    if not box:return None
    x0,y0,x1,y1=box
    pad_x=max(.009,(x1-x0)*.10)
    pad_y=max(.018,(y1-y0)*1.3)
    return (max(0,x0-pad_x),max(0,y0-pad_y),min(1,x1+pad_x),min(1,y1+pad_y))


def centerline_findings(ref_path, check_path, reference, compared):
    left=_center_segments(reference);right=_center_segments(compared)
    # Do not try native coordinate-based pairing when drawing sheets differ.
    # Centerline positions may change if the projected views are rearranged.
    # In that case report a count-only review hypothesis, with no fake location.
    if not _aligned_sheet_space(reference,compared):
        if len(left)==len(right):
            return []
        return [Finding(0,'center','확인 필요','중심선 개수 차이 · 대응 투상도 확인 필요',
            '도면 배치 위치 또는 축척이 서로 달라 직접 좌표 매칭이 불가능합니다. '
            '중심선 객체 개수가 다르지만 분해된 선이나 선종류 설정 차이일 수도 있습니다. '
            '같은 투상도를 직접 연결한 후 누락 여부를 확인하세요.',
            f'기준 중심선 LINE {len(left)}개',f'검토 중심선 LINE {len(right)}개')]
    if not left or len(left)>45 or len(right)>45:
        return []
    candidates=[]
    for i,a in enumerate(left):
        for j,b in enumerate(right):
            score=_match_score(a,b)
            if score is not None:candidates.append((score,i,j))
    matches=[];used_l=set();used_r=set()
    for score,i,j in sorted(candidates):
        if i in used_l or j in used_r:continue
        # Require uniqueness, avoiding a wrong match on repeated centerlines.
        other_i=[s for s,ii,jj in candidates if ii==i and jj!=j]
        other_j=[s for s,ii,jj in candidates if jj==j and ii!=i]
        if (other_i and min(other_i)<score+.035) or (other_j and min(other_j)<score+.035):
            continue
        matches.append((i,j));used_l.add(i);used_r.add(j)
    missing=[a for i,a in enumerate(left) if i not in used_l]
    # Only describe definite *native* centerline mismatch in aligned model coordinates.
    if not missing:return []
    boxes=_highlight_handles(ref_path,[a[4] for a in missing[:8]])
    same_sheet=_aligned_sheet_space(reference,compared)
    findings=[]
    for a in missing[:8]:
        box=_expanded_center_region(boxes.get(a[4]))
        predicted=box if same_sheet else None
        findings.append(Finding(0,'center','확인 필요','기준 중심선의 대응 객체 없음',
             '기준 DXF에서 중심선 선종류의 LINE을 검출했으나 검토 DXF에서 동일 위치·방향·길이의 중심선 객체가 없습니다. '
             + ('두 DXF의 시트 기준선 좌표가 일치해 오른쪽 타원은 중심선이 존재해야 할 예상 위치입니다. '
                '오른쪽에 객체가 실제 검출되었다는 뜻은 아닙니다. ' if predicted else
                '검토 DXF의 대응 위치는 수동으로 지정해 주세요. ')
             + '선이 분해되었거나 다른 선종류로 작성되었다면 확인이 필요합니다.',
             f'중심선 LINE {a[4]}, 길이 {a[2]:.2f}', '대응 중심선 객체 미검출',box,predicted))
    return findings


def _tol_token(entity,doc):
    """Extract dimension nominal + whether a tolerance is visibly printed.

    DIMENSION displayed text is frequently stored in the anonymous geometry
    block, not in dxf.text. Do not classify text "<>" as a literal nominal.
    """
    typ=entity.dxftype()
    fragments=[]
    if typ=='DIMENSION':
        override=_plain(entity.dxf.get('text','') or '').upper()
        geometry=entity.dxf.get('geometry','')
        if geometry and geometry in doc.blocks:
            for child in doc.blocks[geometry]:
                if child.dxftype()=='MTEXT':fragments.append(_plain(child.text))
                elif child.dxftype()=='TEXT':fragments.append(_plain(child.dxf.text))
        if not fragments:return None
        raw=''.join(fragments).upper().replace(' ','')
        nominal_match=re.search(r'\d+(?:\.\d+)?', raw)
        if not nominal_match:return None
        nominal=nominal_match.group(0)
        dia=('%' in override and 'C' in override) or 'Ø' in override or 'Ø' in raw
        tol=any(f.strip() in ('+','-','±') for f in fragments) and sum(
            bool(re.fullmatch(r'\d+\.\d+', f.strip())) for f in fragments)>=1
        tol=tol or '±' in raw or bool(re.search(r'[+-]0\.\d+',raw))
    elif typ in ('MTEXT','TEXT'):
        raw=_plain(entity.text if typ=='MTEXT' else entity.dxf.text).upper().replace(' ','')
        match=re.search(r'(?:Ø|⌀|%%C)?(\d+(?:\.\d+)?)',raw)
        if not match:return None
        nominal=match.group(1)
        dia=any(c in raw for c in ('Ø','⌀','%%C'))
        tol=bool(re.search(r'(?:±\d|[+-]0\.\d)',raw))
    else:
        return None
    return (('Ø' if dia else '') + nominal,bool(tol),raw,str(entity.dxf.handle))


def tolerance_findings(ref_path,check_path,reference,compared):
    # Auto-compare only a uniquely identified nominal per drawing, to prevent
    # pairing unrelated Ø20 dimensions on separate views.
    def extract(doc):
        output=[]
        for ent in doc.modelspace():
            if ent.dxftype() in ('MTEXT','TEXT','DIMENSION'):
                token=_tol_token(ent,doc)
                if token:output.append(token)
        return output
    a=extract(reference);b=extract(compared)
    groups_a={};groups_b={}
    for token in a:groups_a.setdefault(token[0],[]).append(token)
    for token in b:groups_b.setdefault(token[0],[]).append(token)
    diffs=[]
    for key in groups_a.keys()&groups_b.keys():
        aa=groups_a[key];bb=groups_b[key]
        if len(aa)!=1 or len(bb)!=1:continue
        if aa[0][1] and not bb[0][1]:
            diffs.append((key,aa[0],bb[0]))
    if not diffs:return []
    mark_a=_highlight_handles(ref_path,[x[1][3] for x in diffs[:8]])
    mark_b=_highlight_handles(check_path,[x[2][3] for x in diffs[:8]])
    # The anonymous block of a DIMENSION may not expose a stable render handle.
    # In that case use its printed nominal text ONLY if unique across the
    # drawing's dimension blocks; never mark a random repeated Ø20 token.
    from dxf_pair_review import _unique_boxes
    def fallback(path, doc, differences, index, existing):
        nominal_texts=[]
        for ent in doc.modelspace().query('DIMENSION'):
            name=ent.dxf.get('geometry','')
            if name and name in doc.blocks:
                for child in doc.blocks[name]:
                    if child.dxftype() in ('TEXT','MTEXT'):
                        value=_plain(child.text if child.dxftype()=='MTEXT' else child.dxf.text).strip()
                        if value:nominal_texts.append(value)
        wanted=[]
        for _,a,b in differences[:8]:
            token=(a if index==1 else b)
            if token[3] in existing:continue
            nominal=token[0].removeprefix('Ø')
            if nominal_texts.count(nominal)==1:
                wanted.append(nominal)
        boxes=_unique_boxes(path,wanted) if wanted else {}
        for _,a,b in differences[:8]:
            token=(a if index==1 else b)
            nominal=token[0].removeprefix('Ø')
            if nominal in boxes:
                existing.setdefault(token[3],boxes[nominal])
    fallback(ref_path,reference,diffs,1,mark_a)
    fallback(check_path,compared,diffs,2,mark_b)
    return [Finding(0,'tolerance','확인 필요',f'{k} 공차 누락 후보',
             '양쪽 DXF에 동일 공칭치수가 유일하게 존재하며 기준 측에만 공차 문자열이 있습니다. '
             '공차가 별도 객체나 DIMSTYLE에 저장되었을 수 있으므로 추가 확인 후 확정하세요.',
             x[2],y[2],mark_a.get(x[3]),mark_b.get(y[3]))
             for k,x,y in diffs[:8]]

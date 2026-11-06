"""Evidence-first PDF/DXF comparison (non-destructive).

Text/nominal findings are based on *paired* printed PDF and rendered DXF
annotation strings. Visual geometry and centerlines are review candidates,
not metrology or an automatic conformity verdict.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Optional

import ezdxf
import pymupdf

COLOR = {
    "center": "#477BE4",    # blue oval: centerline candidates
    "diameter": "#DA3943",  # red rectangle: diameter/nominal differences
    "dimension": "#DA3943",
    "shape": "#477BE4",     # blue rectangle: geometry candidates
    "tolerance": "#129764", # green rectangle: tolerance difference
    "text": "#B65D14",      # amber rectangle: text callouts
}
CATEGORY = {
    "center": "중심선", "diameter": "지름 Ø", "dimension": "치수",
    "shape": "형상", "tolerance": "공차", "text": "텍스트"
}

@dataclass
class Finding:
    number: int
    kind: str
    verdict: str   # 불일치 / 확인 필요 / 일치
    title: str
    evidence: str
    reference: str = ""
    compared: str = ""
    pdf_box: tuple[float, float, float, float] | None = None   # 0-1 normalized
    dxf_box: tuple[float, float, float, float] | None = None
    dxf_marker: str = ""

    @property
    def color(self):
        return COLOR[self.kind]

    def report_line(self):
        return f"[{self.verdict}] {CATEGORY[self.kind]} · {self.title}\n  PDF: {self.reference or '특정 불가'}\n  DXF: {self.compared or '특정 불가'}\n  근거/추가확인: {self.evidence}"


def _plain(value):
    return (str(value).replace("%%c", "Ø").replace("%%C", "Ø")
            .replace("\\P", " ").replace("\\~", " ").replace("{", "")
            .replace("}", "").replace("\\A1;", "").strip())


def _compact(value):
    return re.sub(r"\s+", "", _plain(value).upper()).replace("×", "X")


def _pdf_box(page, term):
    if not term:
        return None
    hits = page.search_for(str(term).strip())
    if len(hits) != 1:
        return None
    rect = hits[0]
    return (rect.x0 / page.rect.width, rect.y0 / page.rect.height,
            rect.x1 / page.rect.width, rect.y1 / page.rect.height)


def _pdf_callout_value_box(page, parent_box, value):
    """Locate the actual value in a unique callout rather than boxing the whole note.

    Uses the native PDF word boxes; never assigns a position based solely on text
    sequence or a nearby unrelated dimension. Returns None on ambiguity.
    """
    if not parent_box or not value:
        return parent_box
    wanted=_compact(str(value))
    if not wanted:
        return parent_box
    hits=[]
    for x0,y0,x1,y1,raw,*_ in page.get_text('words'):
        norm=_compact(raw)
        if norm!=wanted and not (norm.endswith(wanted) and wanted.startswith('Ø')):
            continue
        cx=(x0+x1)/2/page.rect.width
        cy=(y0+y1)/2/page.rect.height
        if (parent_box[0]-.018 <= cx <= parent_box[2]+.018 and
            parent_box[1]-.012 <= cy <= parent_box[3]+.012):
            hits.append((x0/page.rect.width,y0/page.rect.height,
                         x1/page.rect.width,y1/page.rect.height))
    return hits[0] if len(hits)==1 else parent_box


def _pdf_line_annotations(page):
    lines = []
    for line in page.get_text("text", sort=False).splitlines():
        s = line.strip()
        if s and re.search(r"\b(HOLE|TAP|THRU|DP)\b", s, re.I):
            lines.append((s, _pdf_box(page, s)))
    return lines


def _dxf_dim_rows(doc, dim):
    """Collect displayed geometry-block MTEXT, preserving line breaks and coordinates."""
    try:
        ents = list(doc.blocks[dim.dxf.geometry])
    except (KeyError, AttributeError):
        return []
    rows = []
    for ent in ents:
        if ent.dxftype() not in ("MTEXT", "TEXT"):
            continue
        try:
            raw = ent.text if ent.dxftype() == "MTEXT" else ent.dxf.text
            s = _plain(raw)
            insert = ent.dxf.insert
        except (AttributeError, ValueError):
            continue
        if s.strip():
            rows.append((float(insert.x), float(insert.y), s))
    if not rows:
        return []
    # Single rendered line may be fragmented into several MTEXT entities.
    groups: list[list[tuple[float,float,str]]] = []
    for entry in sorted(rows, key=lambda v: (-v[1], v[0])):
        for group in groups:
            if abs(group[0][1] - entry[1]) < 0.6:
                group.append(entry)
                break
        else:
            groups.append([entry])
    return [("".join(text for x,y,text in sorted(g, key=lambda t:t[0])),
             sorted(g, key=lambda t:t[0])) for g in groups]


def _dxf_annotations(doc):
    annotations = []
    measures = []
    tol_dim_count = 0
    for dim in doc.modelspace().query("DIMENSION"):
        groups = _dxf_dim_rows(doc, dim)
        dim_raw = _plain(dim.dxf.get("text", ""))
        # dimension name can embed \P lines; geometry block is the actual plotted text.
        for combined, ents in groups:
            if re.search(r"\b(HOLE|TAP|THRU|DP)\b", combined, re.I):
                marker = next((s for x,y,s in ents if re.search(r"\b(HOLE|TAP|THRU|DP)\b", s, re.I)), "")
                annotations.append((combined, marker, dim.dxf.handle))
        if re.search(r"\b(HOLE|TAP|THRU|DP)\b", dim_raw, re.I) and not any(a[2] == dim.dxf.handle for a in annotations):
            annotations.append((dim_raw, "", dim.dxf.handle))
        if "HOLE" in dim_raw.upper() or "TAP" in dim_raw.upper():
            continue
        if "<>" not in dim_raw and not re.search(r"\d", dim_raw):
            continue
        # nominal dimension text in the render block; find a numeric value, not the tolerance.
        texts = [s for s,entries in groups]
        numerics = [(s, es) for s,es in groups if re.fullmatch(r"\d+(?:\.\d+)?", _compact(s))]
        nominal = None
        if "<>" in dim_raw:
            candidates=[]
            for row, fragments in groups:
                # '15+0.1' may be emitted as three neighboring MTEXT fragments.
                match=re.match(r"(\d+(?:\.\d+)?)", _compact(row))
                if match:
                    candidates.append((match.group(1),fragments))
            if candidates:
                # Skip tolerance-like small decimals when a nominal exists.
                ordered=sorted(candidates, key=lambda q: (float(q[0]) < 1, -float(q[0])))
                nominal=ordered[0][0]
                numerics = [(x,y) for x,y in numerics if x == nominal] or [(nominal,ordered[0][1])]
        else:
            nominal = re.findall(r"\d+(?:\.\d+)?", dim_raw)
            nominal = nominal[0] if nominal else None
        if not nominal:
            continue
        nominal = ("Ø" if "%%C" in dim.dxf.text.upper() or "Ø" in dim_raw else "") + nominal
        fragments=[s for group, entities in groups for x,y,s in entities]
        has_tolerance = (any(x in fragments for x in ['+','-','±'])
                         and any(re.fullmatch(r"0\.\d+",s.strip()) for s in fragments))
        if has_tolerance:
            tol_dim_count += 1
        marker = numerics[0][0] if numerics else ""
        measures.append((nominal, has_tolerance, marker, dim.dxf.handle))
    return annotations, measures, tol_dim_count


def _label_parts(label):
    s = _compact(label)
    quantity = re.search(r"^(\d+)X", s)
    diams = tuple(re.findall(r"Ø(\d+(?:\.\d+)?)", s))
    dp = re.search(r"\bDP(\d+(?:\.\d+)?)", s)
    # no word boundary between DP and digits (e.g. TAPDP17)
    if not dp:
        dp = re.search(r"DP(\d+(?:\.\d+)?)", s)
    tap = re.search(r"M(\d+(?:\.\d+)?)", s)
    signature = re.sub(r"\d+(?:\.\d+)?", "#", s)
    return {"signature": signature, "qty": quantity.group(1) if quantity else None,
            "diam": diams, "dp": dp.group(1) if dp else None,
            "tap": tap.group(1) if tap else None, "text": s}


def _matching(pdf_items, dxf_items):
    """Only match *unambiguous* callouts by their non-numeric vocabulary.
    A duplicated signature is never used as proof of a particular numeric discrepancy.
    """
    pdf_groups, dxf_groups = defaultdict(list), defaultdict(list)
    for item in pdf_items:
        pdf_groups[_label_parts(item[0])["signature"]].append(item)
    for item in dxf_items:
        dxf_groups[_label_parts(item[0])["signature"]].append(item)
    for signature in sorted(set(pdf_groups) | set(dxf_groups)):
        p, d = pdf_groups[signature], dxf_groups[signature]
        if len(p) == 1 and len(d) == 1:
            yield p[0], d[0], False
        else:
            for entry in p:
                yield entry, None, True
            for entry in d:
                yield None, entry, True


def _pdf_dimension_tokens(page):
    """Nominal candidates only: exclude notes/titleblock; tolerances handled separately."""
    output=[]
    for word in page.get_text("words"):
        x0,y0,x1,y1,s,*_=word
        if y0 > page.rect.height * 0.78 or x0 > page.rect.width * 0.79:
            continue
        if re.fullmatch(r"(?:Ø|R|C)?\d+(?:\.\d+)?", s, re.I):
            output.append((s, (x0/page.rect.width,y0/page.rect.height,x1/page.rect.width,y1/page.rect.height)))
    return output


def _pdf_tolerance_nominals(page, nominal_tokens, tol_pairs):
    """Group printed tolerance signs by the nearest nominal dimension text.

    Conservatively use a 35pt distance gate. PDF coordinate is position-based,
    whereas DXF tolerance metadata is identified from dimension blocks.
    """
    groups=defaultdict(list)
    for sign,value,rect in tol_pairs:
        x=(rect[0]+rect[2])/2
        y=(rect[1]+rect[3])/2
        near=[]
        for i,(nom,box) in enumerate(nominal_tokens):
            if not nom or nom.startswith('0.') or nom=='1':
                continue
            xx=(box[0]+box[2])*page.rect.width/2
            yy=(box[1]+box[3])*page.rect.height/2
            import math
            distance=math.hypot(x-xx,y-yy)
            if distance<=35:
                near.append((distance,i))
        if near:
            nearest=min(near)[1]
            groups[nearest].append((sign,value))
    return groups


def _pdf_tolerance_pairs(page):
    words=page.get_text('words')
    nearby=[]
    for i,(x0,y0,x1,y1,s,*rest) in enumerate(words):
        if not re.fullmatch(r"[+\-±]",s) or y0 > page.rect.height*.80:
            continue
        # a sign must have a decimal value immediately beside/near it, not an unrelated word
        values=[]
        for j,(xx0,yy0,xx1,yy1,t,*_) in enumerate(words):
            if j==i or xx0 < x0-3 or xx0-x1>30 or abs(yy0-y0)>16:
                continue
            if re.fullmatch(r"0\.\d+",t):
                values.append((abs(xx0-x1)+abs(yy0-y0), t))
        if values:
            nearby.append((s, min(values)[1], (x0,y0,x1,y1)))
    return nearby


def check_drawing(pdf_path: Path, dxf_path: Path, selected: set[str] | None=None):
    selected = set(CATEGORY) if selected is None else selected
    findings=[]
    with pymupdf.open(pdf_path) as reference:
        page=reference[0]
        pdf_annotations=_pdf_line_annotations(page)
        pdf_dims=_pdf_dimension_tokens(page)
        # Number tokens inside machining callouts belong to the callout check,
        # not a second, ambiguous general-dimension candidate.
        pdf_dims=[(token,rect) for token,rect in pdf_dims if not any(
            annotation_box and rect[0] >= annotation_box[0]-0.002 and
            rect[2] <= annotation_box[2]+0.002 and
            rect[1] >= annotation_box[1]-0.004 and
            rect[3] <= annotation_box[3]+0.004
            for _,annotation_box in pdf_annotations)]
        pdf_dims=[(token,rect) for token,rect in pdf_dims if not (
            re.fullmatch(r"0\.\d+",token))]
        pdf_tol=_pdf_tolerance_pairs(page)
        pdf_tolerance_groups=_pdf_tolerance_nominals(page,pdf_dims,pdf_tol)
        dxf=ezdxf.readfile(dxf_path)
        dxf_annotations,dxf_dims,dxf_tol_count=_dxf_annotations(dxf)
        # high-confidence: visible callout strings matched unambiguously.
        for p,d,ambiguous in _matching(pdf_annotations,dxf_annotations):
            if ambiguous:
                if 'text' in selected:
                    add = p is not None or d is not None
                    if add:
                        label=(p or d)[0]
                        findings.append(Finding(0, 'text','확인 필요',f'대응 미확인: {label[:34]}',
                                '같은 구조의 표기가 복수이거나 상대 도면에서 확인되지 않습니다. 원본의 실제 위치를 대조하세요.',
                                reference=p[0] if p else '', compared=d[0] if d else '',
                                pdf_box=p[1] if p else None, dxf_marker=d[1] if d else ''))
                continue
            label_p, bbox=p
            label_d, marker, handle=d
            pp,dd=_label_parts(label_p),_label_parts(label_d)
            difference=[]
            if pp['diam'] != dd['diam'] and 'diameter' in selected:
                findings.append(Finding(0,'diameter','불일치','지름 치수 표기 차이',
                        '동일 형식의 홀 표기를 1:1 대응해 Ø 값을 비교했습니다. 실제 3D 형상 직경은 별도 검증해야 합니다.',
                        label_p,label_d,_pdf_callout_value_box(page,bbox,
                            'Ø'+pp['diam'][0] if pp['diam'] else ''),dxf_marker=marker))
            if pp['dp'] != dd['dp'] and 'dimension' in selected:
                findings.append(Finding(0,'dimension','불일치','홀/탭 깊이(DP) 표기 차이',
                        '동일한 표기 문맥(C/B·HOLE/TAP 등)을 확인한 뒤 DP 숫자를 비교했습니다.',
                        label_p,label_d,_pdf_callout_value_box(page,bbox,pp['dp']),dxf_marker=marker))
            if pp['tap'] != dd['tap'] and 'diameter' in selected:
                findings.append(Finding(0,'diameter','불일치','나사 호칭 크기 차이',
                        '동일한 탭 표기의 M 호칭값이 다릅니다. 피치와 유효나사 깊이는 별도 확인 필요.',
                        label_p,label_d,bbox,dxf_marker=marker))
            if pp['qty'] != dd['qty'] and 'shape' in selected:
                findings.append(Finding(0,'shape','불일치','홀/탭 수량 표기 차이',
                        '도면 주기의 수량 표기가 다릅니다. 실제 홀 개수는 CAD 형상에서 추가 확인해야 합니다.',
                        label_p,label_d,bbox,dxf_marker=marker))
            if pp['text'] != dd['text'] and not any(x in selected for x in ('dimension','diameter','shape')):
                if 'text' in selected:
                    findings.append(Finding(0,'text','불일치','가공 지시 텍스트 차이',
                        '두 도면의 동일 주기에서 문구 차이가 확인됩니다.',label_p,label_d,bbox,dxf_marker=marker))
            if pp['text'] != dd['text'] and 'text' in selected:
                # Text differences not already accounted for by dimension/diameter/quantity.
                q1=pp['text'];q2=dd['text']
                for field in ('diam','dp','tap','qty'):
                    if pp[field]!=dd[field]:
                        if field=='diam':
                            for n in pp[field]:q1=q1.replace(n,'#')
                            for n in dd[field]:q2=q2.replace(n,'#')
                        else:
                            if pp[field]:q1=q1.replace(str(pp[field]),'#')
                            if dd[field]:q2=q2.replace(str(dd[field]),'#')
                if q1!=q2:
                    findings.append(Finding(0,'text','확인 필요','가공 지시/문구 차이',
                            '동일 주기의 텍스트를 비교했으나 서식·기호 차이가 포함될 수 있습니다.',
                            label_p,label_d,bbox,dxf_marker=marker))
        # Titleblock/material/part-name textual changes outside DIMENSION blocks.
        # Compare only meaningful stand-alone values: field headings, raw dates,
        # 1-digit counts, and generic notes are intentionally excluded.
        if 'text' in selected:
            import difflib
            standard_headers={
                'SUBNAME','DWG.NO.','TITLE','CUSTOMER','NAMESOFGOODS',
                'MATERIAL','SIZE',"Q'TY",'FINISH/REMARK','DWG.BY',"DES'NBY",
                "CH'DBY",'APPROVED','DWGDATE','SCALE','WEIGHT'
            }
            printed=[t.strip() for t in page.get_text().splitlines() if t.strip()]
            printed_norm=[_compact(t) for t in printed]
            for entity in dxf.modelspace().query('MTEXT TEXT'):
                try:
                    value=entity.text if entity.dxftype()=='MTEXT' else entity.dxf.text
                    value=_plain(value)
                except (ValueError,AttributeError):
                    continue
                tag=_compact(value)
                if (not value or tag in standard_headers or len(tag)<4 or
                    re.fullmatch(r'[\d.\-/+:]+',tag)):
                    continue
                if tag in printed_norm or tag in _compact(page.get_text()):
                    continue
                possibles=[]
                for idx,word in enumerate(printed_norm):
                    if len(word)<4 or abs(len(tag)-len(word))>max(4,len(tag)//2):
                        continue
                    similarity=difflib.SequenceMatcher(None,tag,word).ratio()
                    if similarity>=0.65:
                        possibles.append((similarity,printed[idx]))
                if not possibles:
                    continue  # no defensible 1:1 comparison; do not fabricate a mismatch
                match=max(possibles)[1]
                bbox=_pdf_box(page,match)
                findings.append(Finding(0,'text','확인 필요','도면 정보/문구 차이 후보',
                    'PDF에서 가장 유사한 표기와 DXF 일반 텍스트를 비교했습니다. 해당 위치가 같은 항목인지 검토해야 합니다.',
                    match,value,bbox,dxf_marker=value))
        # Nominal dimensions: safe only when unique and not part of a complex callout.
        if 'dimension' in selected:
            pnum=Counter(_compact(s) for s,_ in pdf_dims)
            dnum=Counter(_compact(s) for s,*_ in dxf_dims)
            # Don't assert mismatches from list counts, e.g. a nominal appears several times
            # in drawing notes or a rendered decomposition changed the tokenization.
            mism=[(v,pc,dnum.get(v,0)) for v,pc in pnum.items() if dnum.get(v,0)==0]
            if mism:
                shown=0
                for num,pc,dc in mism:
                    if shown >= 3: break
                    if num.startswith(('Ø','R','C')) and 'diameter' in selected:
                        kind='diameter'
                    elif num.startswith(('Ø','R','C')):
                        continue
                    else:
                        kind='dimension'
                    if len(num)<2:continue
                    # dimension number alone may come from note, title, or a hole annotation.
                    # retain as evidence candidate rather than false positive.
                    box=next((bbox for s,bbox in pdf_dims if _compact(s)==num),None)
                    findings.append(Finding(0,kind,'확인 필요',f'치수값 {num} 대응 후보',
                             'PDF에 표기된 값에 대응하는 DXF 치수 텍스트를 찾지 못했습니다. 표기 순서·치수 블록 표현을 확인하세요.',
                             num,'동일 표기 자동 검색 실패',None))
                    shown+=1
        # Tolerance warning when structure strongly suggests at least one side missing.
        if 'tolerance' in selected:
            pdf_dimgroups=defaultdict(list)
            dxf_dimgroups=defaultdict(list)
            for idx,(nominal, bbox) in enumerate(pdf_dims):
                pdf_dimgroups[_compact(nominal)].append((bbox,bool(pdf_tolerance_groups.get(idx))))
            for nominal,has_tol,marker,handle in dxf_dims:
                dxf_dimgroups[_compact(nominal)].append((has_tol,marker))
            # Avoid allocating a PDF tolerance to the wrong DXF dimension when
            # its nearest PDF nominal has no corresponding DXF nominal token.
            uncertain_assignment=any(
                _compact(pdf_dims[idx][0]) not in dxf_dimgroups
                for idx, signs in pdf_tolerance_groups.items() if signs)
            for nominal in sorted(set(pdf_dimgroups)&set(dxf_dimgroups)):
                if uncertain_assignment:
                    break
                pgroup,dgroup=pdf_dimgroups[nominal],dxf_dimgroups[nominal]
                if len(pgroup)!=len(dgroup):
                    continue # cannot isolate a particular dimension
                p_tols=sum(tol for _,tol in pgroup)
                d_tols=sum(tol for tol,_ in dgroup)
                if p_tols==d_tols:
                    continue
                box=next((box for box,has in pgroup if has),pgroup[0][0])
                marker=next((m for has,m in dgroup if has and m),dgroup[0][1])
                missing_side='검토 DXF' if p_tols>d_tols else '기준 PDF'
                findings.append(Finding(0,'tolerance','확인 필요',f'{nominal} 치수 공차 누락 후보',
                   f'같은 호칭 치수가 각 도면에 {len(pgroup)}개 있고, 공차를 동반한 치수는 '
                   f'PDF {p_tols}개 / DXF {d_tols}개입니다. {missing_side}에서 공차 누락 또는 '
                   '표기방식 차이를 확인하세요. 위치 대응 확정 전에는 자동 수정하지 않습니다.',
                   f'{nominal} · 공차 치수 {p_tols}개',f'{nominal} · 공차 치수 {d_tols}개',
                   box,dxf_marker=marker))
            # Count unique sign+decimal markers by approximate location; repeated symbol within
            # a single stacked tolerance pair is not individually one whole dimension.
            pdf_tol_count=len(pdf_tol)
            if pdf_tol_count and not dxf_tol_count and not any(f.kind=='tolerance' for f in findings):
                findings.append(Finding(0,'tolerance','확인 필요','DXF 공차 누락 의심',
                    f'PDF에서 부호+소수 치수 요소 {pdf_tol_count}개가 감지되지만 DXF 치수 블록에서 명시적 공차를 찾지 못했습니다. '
                    '텍스트가 분리된 방식인지 확인하세요.',
                    f'공차 부호·값 단서 {pdf_tol_count}개','치수 블록 공차 0개'))
            elif dxf_tol_count and not pdf_tol_count and not any(f.kind=='tolerance' for f in findings):
                findings.append(Finding(0,'tolerance','확인 필요','PDF 공차 누락 의심',
                    f'DXF에서 공차가 있는 치수 블록 {dxf_tol_count}개를 확인했지만 PDF 텍스트에서 부호·값을 추출하지 못했습니다. '
                    'PDF 글꼴 또는 공차 표기 방식을 확인하세요.',
                    '부호+소수 형태 추출 안 됨', f'공차 치수 블록 {dxf_tol_count}개'))
        # Centerlines: name-/style-based evidence, not a proof of missing rendered line.
        if 'center' in selected:
            center=[]
            for ent in dxf.modelspace():
                lt=str(ent.dxf.get('linetype','')).upper()
                layer=str(ent.dxf.get('layer','')).upper()
                if any(k in lt or k in layer for k in ('CENTER','중심','일점쇄선')):
                    center.append(ent)
            drawings=page.get_drawings()
            # Native PDF path operators commonly flatten dashes into individual segments.
            # Thus PDF path count cannot reliably establish a missing center line.
            findings.append(Finding(0,'center','확인 필요','중심선 표시 및 누락 검토',
                f'DXF 모델 공간에서 중심선 명칭/선종류 객체 {len(center)}개를 확인했습니다. '
                f'PDF 벡터 경로 {len(drawings)}개는 출력 시 선종류가 펼쳐질 수 있어 수량만으로 누락을 확정할 수 없습니다. '
                '중심선이 있어야 하는 뷰의 홀 중심을 직접 비교하세요.',
                'PDF 시각 확인 필요',f'중심선 관련 객체 {len(center)}개'))
        # Location-bearing raster comparison: registration and unmatched strokes
        # become candidates only, never a definitive CAD geometry judgement.
        if 'shape' in selected or 'center' in selected:
            from visual_difference import find_visual_candidates
            visual_items, visual_status = find_visual_candidates(pdf_path,dxf_path)
            # Restrict highlighted geometric candidates to projection regions
            # paired by both position and size. Non-paired projections are
            # reported for review instead of drawing potentially false boxes.
            try:
                from geometry_semantics import semantic_report
                _, pdf_views, dxf_views, view_pairs = semantic_report(pdf_path,dxf_path)
                def within_view(box, view_box, pad=.022):
                    x=(box[0]+box[2])*.5
                    y=(box[1]+box[3])*.5
                    return (view_box[0]-pad<=x<=view_box[2]+pad and
                            view_box[1]-pad<=y<=view_box[3]+pad)
                reliable=[]
                for region in visual_items:
                    if any(within_view(region['pdf_box'], p.box) and
                           within_view(region['dxf_box'], d.box)
                           for p,d in view_pairs):
                        reliable.append(region)
                filtered=len(visual_items)-len(reliable)
                visual_items=reliable
                visual_status += (f' 투상도 대응 {len(view_pairs)}쌍 검증 후 '
                                  f'위치 불확실한 시각 후보 {filtered}건 표시 보류.')
            except (ImportError, OSError, ValueError) as ex:
                # Never revert to unverified overlays if view evidence cannot be loaded.
                visual_items=[]
                visual_status += f' 투상도 검증 불가로 형상 위치 표시 보류: {ex}'
            for rank, item in enumerate(visual_items,1):
                findings.append(Finding(0, 'shape','확인 필요',
                    f'시각 차이 영역 {rank} · PDF/DXF 정렬 비교',
                    '정렬된 도면에서 문자·주기·표제란을 제외한 비문자 선 차이를 탐색했습니다. '
                    f"차이 픽셀 {item['pixels']}개. 도면의 선 굵기·폰트·렌더링 차이일 수 있으므로 "
                    '실제 형상 변경인지 중심선/숨은선/렌더링 차이인지 검토하세요. '
                    +visual_status,
                    'PDF 시각 차이 후보', 'DXF 시각 차이 후보',
                    item['pdf_box'],item['dxf_box']))
            if not visual_items:
                findings.append(Finding(0,'shape','확인 필요','비문자 형상 차이 자동 위치 미검출',
                    visual_status + ' · 파일이 같다는 의미가 아닙니다. 미표시 형상과 중심선은 확대해 직접 확인하세요.',
                    '자동 위치 특정 불가','자동 위치 특정 불가'))
        if 'shape' in selected:
            visible=Counter(ent.dxftype() for ent in dxf.modelspace())
            geom={key:visible.get(key,0) for key in ('LINE','ARC','CIRCLE','LWPOLYLINE','SPLINE')}
            if not any(x.title.startswith('시각 차이 영역') for x in findings):
                findings.append(Finding(0,'shape','확인 필요','2D 형상/투영 대응 검토',
                f'DXF 모델 공간 기본 형상 객체: {geom}. '
                '기준 PDF의 투영 위치·축척을 맞춘 뒤 원호, 외곽, 홀 개수, 숨은선의 차이를 확인해야 합니다. '
                'PDF 출력 경로의 개수와 DXF CAD 객체 개수는 직접 비교할 수 없습니다.',
                'PDF 도면 투영 및 형상 확인 필요',str(geom)))
    # Apply only high-confidence sheet registration to uniquely located PDF
    # text findings. We label these as *estimated corresponding DXF regions*;
    # they are not proofs that the precise DXF glyph was independently found.
    mapped_candidates=[f for f in findings if f.pdf_box and not f.dxf_box and
                       f.verdict=='불일치' and f.kind in ('dimension','diameter','text')]
    if mapped_candidates:
        try:
            from visual_difference import register_annotation_boxes
            mapped,diagnostic=register_annotation_boxes(pdf_path,dxf_path,
                         [f.pdf_box for f in mapped_candidates])
            for f,box in zip(mapped_candidates,mapped):
                if box:
                    f.dxf_box=box
                    f.evidence += (' DXF 표시 위치는 PDF·DXF 시트 정렬에 근거한 '
                                   '추정 영역이며, 실제 DXF 개별 문자의 좌표 검출은 아닙니다. '
                                   + diagnostic)
        except (ImportError, OSError, ValueError) as exc:
            pass  # Missing or weak registration must never fabricate a box.

    # sort: actionable clear discrepancies at the top, then candidates.
    order={'불일치':0,'확인 필요':1,'일치':2}
    findings.sort(key=lambda f:(order[f.verdict],list(CATEGORY).index(f.kind)))
    for index,item in enumerate(findings,1):
        item.number=index
    return findings


def report_text(findings,pdf,dxf):
    count=Counter(x.verdict for x in findings)
    lines=["[PDF·DXF 도면 전체 검토 보고서]",
           "검토 범위: 가공 지시/지름/깊이/탭/수량, 일반 치수, 공차 표기, 도면 문자, 중심선 후보, 비문자 형상 차이",
           "판정 원칙: 유일한 1:1 표기만 자동 불일치 판정하며, 위치 미확인 항목에는 임의 표시를 하지 않습니다.",
           f"기준 PDF: {Path(pdf).name}",f"검토 DXF: {Path(dxf).name}",
           f"표기 불일치 {count['불일치']}건 / 확인 필요 {count['확인 필요']}건",
           "※ PDF/DXF 표기 비교이며 실제 STP 형상·공차의 적합 판정은 아닙니다.", ""]
    lines.extend(f"{f.number}. {f.report_line()}\n" for f in findings)
    return '\n'.join(lines)

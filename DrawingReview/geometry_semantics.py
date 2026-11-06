"""Read-only view grouping and dimension/shape diagnostics for native PDF / DXF.

A view region is a *candidate* inferred from drawing whitespace, not proof of a
projection. Distances in pixels are never interpreted as drawing measurements.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
import re

import ezdxf
import pymupdf

from export_pdf import render_dxf_pdf

@dataclass
class ViewRegion:
    index: int
    box: tuple[float, float, float, float]  # normalized document coordinates
    shape_count: int = 0
    circle_count: int = 0
    arc_count: int = 0
    line_count: int = 0
    dimension_tokens: tuple[str, ...] = ()
    name: str = '투상도 후보'


def classify_dimension(value: str) -> str:
    """Classification by drawing symbol, not by a bare number alone."""
    raw=str(value).strip().upper().replace('%%C','Ø').replace('⌀','Ø').replace('Φ','Ø')
    if re.search(r'(?:Ø|\\\\[Uu]\+2205)\s*\d', raw):
        return '지름'
    if re.search(r'\bR\s*\d', raw):
        return '반경'
    if re.search(r'\d\s*°|\d\s*DEG\b', raw):
        return '각도'
    if re.search(r'\b(?:DP|DEPTH)\s*\d',raw):
        return '깊이'
    if re.search(r'(?:M\d+(?:\s*[X×]\s*\d+(?:\.\d+)?)?)',raw):
        return '탭/나사'
    if re.search(r'(?<![A-Z])\d+(?:\.\d+)?',raw):
        return '길이 후보'
    return '기타'


def _native_page(path: Path, dxf: bool):
    if dxf:
        return pymupdf.open(stream=render_dxf_pdf(path),filetype='pdf')
    return pymupdf.open(path)


def _detect_views(page, max_views=12):
    """Segment main projected shapes from whitespace, excluding note/title regions.

    OCR and annotation text are masked before searching components; this avoids
    mistaking a dimension leader or title-block table for a projected part.
    """
    import cv2
    import numpy as np
    scale=1400/max(page.rect.width,page.rect.height)
    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),colorspace=pymupdf.csGRAY,alpha=False)
    gray=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width)
    dark=(gray<155).astype(np.uint8)*255
    h,w=dark.shape
    # CAD drawings in this application use a border, notes and titleblock.
    # Leave a meaningful inset and bottom margin to avoid these non-view regions.
    roi=np.zeros_like(dark)
    roi[int(h*.11):int(h*.76),int(w*.10):int(w*.88)]=255
    dark=cv2.bitwise_and(dark,roi)
    for x0,y0,x1,y1,*_ in page.get_text('words'):
        a=max(0,int(x0*scale)-3);b=max(0,int(y0*scale)-3)
        c=min(w,int(x1*scale)+3);d=min(h,int(y1*scale)+3)
        if c>a and d>b:
            dark[b:d,a:c]=0
    # Remove dimension line fragments as much as possible while keeping solid outlines.
    closed=cv2.morphologyEx(dark,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_RECT,(5,5)))
    n,lab,stats,centers=cv2.connectedComponentsWithStats(closed,8)
    boxes=[]
    for x,y,bw,bh,area in stats[1:]:
        x,y,bw,bh,area=map(int,(x,y,bw,bh,area))
        if area<25 or bw<12 or bh<12 or bw>w*.36 or bh>h*.48:
            continue
        boxes.append([x,y,x+bw,y+bh,area])
    # Combine nearby pieces of one projection. A large white gap remains a view separator.
    margin=max(8,int(min(w,h)*.015))
    parent=list(range(len(boxes)))
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,a in enumerate(boxes):
        for j in range(i):
            b=boxes[j]
            hx=max(a[0]-b[2],b[0]-a[2],0)
            hy=max(a[1]-b[3],b[1]-a[3],0)
            if hx<=margin and hy<=margin:
                parent[find(i)]=find(j)
    groups={}
    for i,b in enumerate(boxes):
        k=find(i)
        if k not in groups:groups[k]=b[:4]
        else:
            g=groups[k];g[:]=[min(g[0],b[0]),min(g[1],b[1]),max(g[2],b[2]),max(g[3],b[3])]
    grouped=[]
    for x0,y0,x1,y1 in groups.values():
        if x1-x0<25 or y1-y0<25:continue
        grouped.append((x0/w,y0/h,x1/w,y1/h))
    grouped.sort(key=lambda b: (round((b[1]+b[3])/2,1),(b[0]+b[2])/2))
    # Limit to conspicuous regions. Too many fragments => no confident views.
    return grouped[:max_views],len(grouped)


def _pdf_dimension_tokens(page):
    out=[]
    for x0,y0,x1,y1,t,*_ in page.get_text('words'):
        if x0>page.rect.width*.88 or y0>page.rect.height*.77:continue
        s=str(t).strip()
        if re.fullmatch(r'(?:Ø|⌀|Φ|R|C|M)?\s*\d+(?:\.\d+)?(?:°)?',s,re.I):
            cx=(x0+x1)/(2*page.rect.width)
            cy=(y0+y1)/(2*page.rect.height)
            out.append((s,cx,cy))
    return out


def analyze_views(path:Path,dxf:bool=False):
    path=Path(path)
    with _native_page(path,dxf) as doc:
        page=doc[0]
        bounds, region_total=_detect_views(page)
        tokens=_pdf_dimension_tokens(page)
        result=[]
        for n,b in enumerate(bounds,1):
            words=tuple(t for t,x,y in tokens if b[0]-.025<=x<=b[2]+.025 and b[1]-.025<=y<=b[3]+.025)
            result.append(ViewRegion(n,b,dimension_tokens=words))
        return {'file':path.name,'source':'DXF CAD → 벡터 PDF' if dxf else 'PDF 벡터',
                'views':result,'regions_found':region_total,
                'dimensions':Counter(classify_dimension(t) for t,_,_ in tokens),
                'tokens':tokens,'page_size':(page.rect.width,page.rect.height)}


def analyze_dxf_geometry(path:Path):
    doc=ezdxf.readfile(path)
    counts=Counter(e.dxftype() for e in doc.modelspace())
    layer_counts=Counter(str(e.dxf.get('layer','')) for e in doc.modelspace())
    center=Counter()
    for e in doc.modelspace():
        lname=str(e.dxf.get('layer','')).upper()
        lt=str(e.dxf.get('linetype','')).upper()
        if any(k in (lname+' '+lt) for k in ('CENTER','CENTER2','중심')):
            center[e.dxftype()]+=1
    actual_dimensions=[]
    for e in doc.modelspace().query('DIMENSION'):
        raw=str(e.dxf.get('text','')).replace('%%c','Ø').replace('%%C','Ø')
        kind=classify_dimension(raw)
        actual_dimensions.append((e.dxf.handle,kind,raw,e.dxf.get('dimtype',None)))
    return {'counts':counts,'layers':layer_counts,'center_entities':center,
            'dimensions':actual_dimensions}


def compare_view_structure(pdf_path:Path,dxf_path:Path):
    pdf=analyze_views(pdf_path)
    dxf=analyze_views(dxf_path,True)
    geom=analyze_dxf_geometry(dxf_path)
    lines=['[투상도 구분 및 형상 의미 분석]','기준 PDF: '+pdf['file'],
           '검토 DXF: '+dxf['file'],
           f"PDF 투상도 후보 {len(pdf['views'])}개 / DXF 투상도 후보 {len(dxf['views'])}개",
           '주의: 도면 여백과 벡터선 연결성을 이용한 후보이며 투상도 명칭 확정은 아닙니다.',
           'Ø = 지름 후보, R = 반경, DP = 깊이, M = 탭/나사, 무기호 숫자 = 길이 후보',
           '무기호 숫자에는 수량·주기 숫자도 포함될 수 있으므로 자동 판정 금지.',
           '', '[PDF 투상도 후보]']
    for v in pdf['views']:
        label=', '.join(v.dimension_tokens[:12]) or '내부 치수 문자 없음'
        lines.append(f'  #{v.index} 위치 {tuple(round(x,3) for x in v.box)} · 치수 후보: {label}')
    lines.append('[DXF 투상도 후보]')
    for v in dxf['views']:
        label=', '.join(v.dimension_tokens[:12]) or '내부 치수 문자 없음'
        lines.append(f'  #{v.index} 위치 {tuple(round(x,3) for x in v.box)} · 치수 후보: {label}')
    lines.append('[DXF 실제 CAD 객체]')
    lines.append('  '+', '.join(f'{k} {v}' for k,v in sorted(geom['counts'].items())))
    lines.append('  중심선 선종류/레이어 후보 '+str(sum(geom['center_entities'].values()))+'개')
    lines.append('  실제 치수 객체 구분: '+str(dict(Counter(x[1] for x in geom['dimensions']))))
    if len(pdf['views'])!=len(dxf['views']):
        lines.append('※ 투상도 후보 수가 다릅니다. 기하 형상 불일치라고 단정하지 마세요.')
    lines.append('※ 같은 투상도와 치수가 대응됨을 확인하기 전에는 차이 박스/자동 수정을 생성하지 않습니다.')
    return '\n'.join(lines),pdf,dxf,geom


def pair_views(pdf_data, dxf_data):
    """Mutual-nearest projection pairing; ambiguous/merged views are unpaired."""
    import math
    a=pdf_data['views'];b=dxf_data['views']
    def score(x,y):
        ax=(x.box[0]+x.box[2])/2;ay=(x.box[1]+x.box[3])/2
        bx=(y.box[0]+y.box[2])/2;by=(y.box[1]+y.box[3])/2
        center=math.hypot(ax-bx,ay-by)
        aw=max(.001,x.box[2]-x.box[0]);ah=max(.001,x.box[3]-x.box[1])
        bw=max(.001,y.box[2]-y.box[0]);bh=max(.001,y.box[3]-y.box[1])
        # Do not merge a 2-view rectangle onto one view.
        similarity=max(aw/bw,bw/aw,ah/bh,bh/ah)
        return center if center<.09 and similarity<1.85 else 999.
    matches=[]
    for x in a:
        scores=sorted(((score(x,y),y.index,y) for y in b),key=lambda q:q[0])
        if not scores or scores[0][0]>=999:continue
        s,_,y=scores[0]
        if len(scores)>1 and scores[1][0]-s<.028:continue
        reverses=sorted((score(z,y),z.index) for z in a)
        if reverses[0][1]!=x.index or (len(reverses)>1 and reverses[1][0]-reverses[0][0]<.028):
            continue
        matches.append((x,y))
    return matches


def semantic_report(pdf_path,dxf_path):
    initial,pdf,dxf,geom=compare_view_structure(pdf_path,dxf_path)
    pairs=pair_views(pdf,dxf)
    groups=['\n[투상도 1:1 대응 (기하 위치 근거)]']
    if not pairs:
        groups.append('도면 출력 위치·크기 근거가 불충분하여 자동 대응 보류')
    for p,d in pairs:
        pcounts=Counter(classify_dimension(t) for t in p.dimension_tokens)
        dcounts=Counter(classify_dimension(t) for t in d.dimension_tokens)
        groups.append(f'PDF 후보 #{p.index} ↔ DXF 후보 #{d.index} · 비교 치수 유형 PDF {dict(pcounts)}, DXF {dict(dcounts)}')
        # A count discrepancy isn't an error: embedded fonts can fragment words.
    groups += ['※ 동일 숫자라고 해도 위치·치수선 대응이 확인되지 않으면 일치 판정하지 않습니다.',
               '※ 자동 분리 실패한 투상도는 수동 영역 지정 또는 원본 CATIA 뷰 메타데이터가 필요합니다.']
    return initial+'\n'+'\n'.join(groups),pdf,dxf,pairs

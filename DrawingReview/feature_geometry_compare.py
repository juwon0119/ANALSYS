"""Read-only relative-geometry matcher for two native DXF drawings.

This module links distinctive clusters of circle centers under translation,
rotation and uniform *drawing* scale. It compares circles and attached line
segments only when a unique geometrical registration is supported. It does
not claim CAD dimensions from a printed drawing scale; all measured lengths
are modelspace values and display changes may reflect an altered plot scale.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
import math
from statistics import median
from difference_review import Finding

@dataclass(frozen=True)
class Ring:
    x:float
    y:float
    r:float
    handle:str


def _rings(doc):
    output=[]
    for ent in doc.modelspace():
        if ent.dxftype() == 'CIRCLE':
            try:
                x,y=float(ent.dxf.center.x),float(ent.dxf.center.y)
                r=float(ent.dxf.radius)
                if math.isfinite(r) and r>1e-6:
                    output.append(Ring(x,y,r,str(ent.dxf.handle)))
            except (AttributeError,TypeError,ValueError):
                continue
    return output


def _groups(rings):
    """Proximity-connected clusters of distinct center locations.

    Inner/outer concentric circles form one center, not multiple hole centers.
    Isolated circles cannot be independently matched under arbitrary movement.
    """
    centers=defaultdict(list)
    for c in rings:
        centers[(round(c.x,5),round(c.y,5))].append(c)
    points=list(centers)
    if len(points)<3:
        return []
    nearest=[]
    for i,p in enumerate(points):
        candidates=[math.dist(p,q) for j,q in enumerate(points) if i!=j]
        nearest.append(min(candidates))
    typical=max(median(nearest),1e-6)
    cutoff=typical*2.7
    pending=set(points); result=[]
    while pending:
        seed=min(pending);pending.remove(seed)
        todo=[seed];group=[seed]
        while todo:
            p=todo.pop()
            near={q for q in pending if math.dist(p,q)<=cutoff}
            pending-=near;todo.extend(near);group.extend(near)
        if len(group)>=3:
            result.append({p:centers[p] for p in group})
    return result


def _signature(group):
    pts=list(group)
    distances=sorted(math.dist(p,q) for p,q in combinations(pts,2))
    scale=max(median(distances),1e-6)
    return len(pts),[d/scale for d in distances],scale


def _similar_signature(a,b):
    if a[0]!=b[0] or a[0]<3:return False
    return max(abs(x-y) for x,y in zip(a[1],b[1]))<=0.035


def _transform(source_a,source_b,target_a,target_b):
    sx=source_b[0]-source_a[0];sy=source_b[1]-source_a[1]
    tx=target_b[0]-target_a[0];ty=target_b[1]-target_a[1]
    length=math.hypot(sx,sy)
    if length<1e-7:return None
    ratio=math.hypot(tx,ty)/length
    a=math.atan2(ty,tx)-math.atan2(sy,sx)
    c,s=math.cos(a),math.sin(a)
    def project(p):
        x,y=p[0]-source_a[0],p[1]-source_a[1]
        return (target_a[0]+ratio*(x*c-y*s),target_a[1]+ratio*(x*s+y*c))
    return project,ratio


def _match_group(a,b):
    """Register group using pair anchors; only return unique full point map."""
    if len(a)!=len(b) or len(a)>16:return None
    pa=list(a);pb=list(b)
    sa=_signature(a);sb=_signature(b)
    if not _similar_signature(sa,sb):return None
    tol=max(0.025*sb[2],1e-5)
    candidates={}
    for x1,x2 in combinations(pa,2):
        for y1,y2 in combinations(pb,2):
            for start,end in ((y1,y2),(y2,y1)):
                trans=_transform(x1,x2,start,end)
                if trans is None:continue
                project,scale=trans
                if not 0.01<scale<100:continue
                assigned={};used=set();errors=[]
                for p in pa:
                    expected=project(p)
                    options=sorted((math.dist(expected,q),q) for q in pb if q not in used)
                    if not options or options[0][0]>tol:break
                    d,q=options[0]
                    if len(options)>1 and abs(options[1][0]-d)<tol*.2:break
                    assigned[p]=q;used.add(q);errors.append(d)
                if len(assigned)!=len(pa):continue
                key=tuple(sorted((p,assigned[p]) for p in pa))
                candidates[key]=(assigned,scale,median(errors))
    # Symmetric layouts (e.g. a rectangular 4-hole pattern) can have multiple
    # equally valid mappings. Never guess which individual hole is which.
    if len(candidates)!=1:return None
    return next(iter(candidates.values()))


def _line_lengths_by_group(doc, group, center_scale):
    """Candidate straight edges that sit close to the group; never use to infer dimension."""
    points=list(group)
    xmin=min(p[0] for p in points);xmax=max(p[0] for p in points)
    ymin=min(p[1] for p in points);ymax=max(p[1] for p in points)
    pad=center_scale*.22
    lengths=[]
    for ent in doc.modelspace():
        if ent.dxftype()!='LINE':continue
        try:
            a=(float(ent.dxf.start.x),float(ent.dxf.start.y))
            b=(float(ent.dxf.end.x),float(ent.dxf.end.y))
            mid=((a[0]+b[0])/2,(a[1]+b[1])/2)
            if xmin-pad<=mid[0]<=xmax+pad and ymin-pad<=mid[1]<=ymax+pad:
                length=math.dist(a,b)
                if length>1e-5:lengths.append(length/center_scale)
        except (AttributeError,ValueError):pass
    return sorted(lengths)


def _lines_consistent(la,lb):
    if not la and not lb:return True
    if len(la)!=len(lb):return False
    # Ignore micro differences in CAD construction/rounding.
    return all(abs(x-y)<=max(.04,.035*max(x,y)) for x,y in zip(la,lb))


def _dimensions_near_group(doc, group, characteristic):
    """Nearby native dimension objects, kept distinct from actual geometry.

    Association is tentative: dimension definition points can live outside
    the view and the label may refer to a different projected feature.
    """
    points=list(group)
    x0=min(v[0] for v in points)-characteristic*.65
    x1=max(v[0] for v in points)+characteristic*.65
    y0=min(v[1] for v in points)-characteristic*.65
    y1=max(v[1] for v in points)+characteristic*.65
    out=[]
    for ent in doc.modelspace():
        if ent.dxftype()!='DIMENSION':continue
        try:
            pos=ent.dxf.get('defpoint')
            if pos is None:continue
            x,y=float(pos.x),float(pos.y)
            if not (x0<=x<=x1 and y0<=y<=y1):continue
            measurement=float(ent.get_measurement())
            if not math.isfinite(measurement):continue
            kind=int(ent.dxf.dimtype)&7
            if kind not in (0,1,2,3,4):continue
            raw=str(ent.dxf.get('text','') or '').strip()
            # Literal override is compared separately; '<>' is dynamic size.
            out.append((ent,kind,(x,y),measurement,raw))
        except (AttributeError,TypeError,ValueError,OverflowError):continue
    return out


def _displayed_dimension(value,measurement):
    raw=str(value).replace('%%c','Ø').replace('%%C','Ø').strip()
    if not raw or raw=='<>':return f'{measurement:.4f}'
    if '<>' in raw:return raw.replace('<>',f'{measurement:.4f}')
    return raw


def _associated_dimension_findings(a_doc,b_doc,group_a,group_b,scale_a,scale_b,pairs,plot_scale):
    """Geometry-anchored native DIMENSION comparison without position guessing.

    Only dimensions with uniquely matched definition points are compared.
    Text displayed on a rendered DIMENSION anonymous block may contain
    distinct tolerances and remains a separate tolerance review concern.
    """
    a=_dimensions_near_group(a_doc,group_a,scale_a)
    b=_dimensions_near_group(b_doc,group_b,scale_b)
    if not a or not b:return []
    base=list(pairs)
    if len(base)<2:return []
    first=base[0]
    second=max(base[1:],key=lambda p: math.dist(first,p))
    reg=_transform(first,second,pairs[first],pairs[second])
    if reg is None:return []
    project,_=reg
    candidates=[]
    for ea,ka,pa,ma,ta in a:
        q=project(pa)
        possible=[]
        for eb,kb,pb,mb,tb in b:
            if ka!=kb:continue
            dist=math.dist(q,pb)
            if dist<=max(.1,.06*scale_b):
                possible.append((dist,eb,kb,pb,mb,tb))
        possible.sort(key=lambda z:z[0])
        if len(possible)!=1:continue
        _,eb,kb,pb,mb,tb=possible[0]
        candidates.append((ea,eb,ka,ma,mb,ta,tb))
    counts_a=defaultdict(int);counts_b=defaultdict(int)
    for x,y,*_ in candidates:
        counts_a[x.dxf.handle]+=1;counts_b[y.dxf.handle]+=1
    output=[]
    for ea,eb,kind,ma,mb,ta,tb in candidates:
        if counts_a[ea.dxf.handle]!=1 or counts_b[eb.dxf.handle]!=1:continue
        norm_a=ma/scale_a;norm_b=mb/scale_b
        # A uniform plot scale changes raw modelspace values. Compare ratios.
        if abs(norm_a-norm_b)>max(.02,.025*max(abs(norm_a),abs(norm_b))):
            output.append(Finding(0,'dimension','확인 필요','형상에 연관된 치수 측정값 차이 후보',
                '동일한 원 중심 배치로 정렬된 투상도 영역에서 DIMENSION 정의점과 '
                '치수 형식을 함께 대응시켰습니다. 모델 공간의 측정값과 표기값은 '
                '별개이며, 실제 연관 형상은 검토자가 확인해야 합니다.',
                f'{ea.dxf.handle}: 측정 {ma:.4f}, 표시 {_displayed_dimension(ta,ma)}',
                f'{eb.dxf.handle}: 측정 {mb:.4f}, 표시 {_displayed_dimension(tb,mb)}'))
        elif ta.strip() not in ('','<>') and tb.strip() not in ('','<>') and ta.strip()!=tb.strip():
            output.append(Finding(0,'dimension','확인 필요','형상 연결 치수의 표기 재검토',
                '기하 대응 DIMENSION의 표시 문자열이 다르지만 공차·주기 또는 '
                '문자열 서식일 수 있습니다. 원본 객체를 확인하세요.',
                f'{ea.dxf.handle}: {ta}',f'{eb.dxf.handle}: {tb}'))
    return output[:10]


def compare_native_geometry(ref,check,limit=25,ref_path=None,check_path=None):
    """Generate conservative candidates from shape correspondence, not page position.

    No guessed overlay position is returned here. Associated CAD handles are
    reported as evidence; exact view-local display marking is a separate job.
    """
    left=_groups(_rings(ref));right=_groups(_rings(check))
    ls=[_signature(g) for g in left];rs=[_signature(g) for g in right]
    findings=[]
    for i,group in enumerate(left):
        possibilities=[j for j,signature in enumerate(rs) if _similar_signature(ls[i],signature)]
        if len(possibilities)!=1:continue
        j=possibilities[0]
        if sum(_similar_signature(s,rs[j]) for s in ls)!=1:continue
        matched=_match_group(group,right[j])
        if matched is None:continue
        pairs,scale,error=matched
        left_scale,right_scale=ls[i][2],rs[j][2]
        # Compare concentric circle geometry at each corresponding center.
        for p,q in pairs.items():
            left_r=sorted(group[p],key=lambda c:c.r)
            right_r=sorted(right[j][q],key=lambda c:c.r)
            if len(left_r)!=len(right_r):
                findings.append(Finding(0,'shape','확인 필요','대응 원형 요소 개수 차이',
                    f'중심 배치의 거리비가 고유하게 대응됨 (배율 약 {scale:.4g}, 정합 오차 {error:.4g}). '
                    '동심원/카운터보어/가공기호 중 무엇인지 확인하세요. 도면상 위치를 임의로 표시하지 않습니다.',
                    f'기준 중심 {p} · 원 {len(left_r)}개 · 핸들 {[v.handle for v in left_r]}',
                    f'검토 중심 {q} · 원 {len(right_r)}개 · 핸들 {[v.handle for v in right_r]}'))
                continue
            for a,b in zip(left_r,right_r):
                norm_a=a.r/left_scale;norm_b=b.r/right_scale
                if abs(norm_a-norm_b)>max(.012,.035*max(norm_a,norm_b)):
                    findings.append(Finding(0,'shape','확인 필요','동일 형상군 원 반경 관계 차이',
                        f'다른 시트 위치와 균일 축척을 허용해 원 중심 배치를 일대일 대응함. '
                        f'기준/검토 도면 배율은 {scale:.4g}배. '
                        '상대 반경 차이는 형상 후보이며 실제 홀 직경·치수 공차는 연관된 치수 표기를 확인해야 합니다.',
                        f'원 핸들 {a.handle}, 반경 {a.r:.4f}, 상대 반경 {norm_a:.4f}',
                        f'원 핸들 {b.handle}, 반경 {b.r:.4f}, 상대 반경 {norm_b:.4f}'))
            if len(findings)>=limit:return findings
        findings.extend(_associated_dimension_findings(ref,check,group,right[j],
                                                        left_scale,right_scale,pairs,scale))
        if len(findings)>=limit:return findings[:limit]
        # Compare normalized line-length distribution within uniquely paired center cluster.
        la=_line_lengths_by_group(ref,group,left_scale)
        lb=_line_lengths_by_group(check,right[j],right_scale)
        if not _lines_consistent(la,lb):
            findings.append(Finding(0,'shape','확인 필요','대응 형상군 선분 구성 차이',
                '원 중심 관계로 고유하게 대응된 형상군 내에서 직선 길이비 분포가 다릅니다. '
                '치수선이나 숨은선도 포함될 수 있어 확정 오류가 아닙니다.',
                f'기준 직선 {len(la)}개, 길이비 {la[:8]}',
                f'검토 직선 {len(lb)}개, 길이비 {lb[:8]}'))
        if len(findings)>=limit:return findings[:limit]
    return findings


def attach_verified_dxf_positions(findings,reference_path,compared_path):
    """Resolve positions only from uniquely identified CAD handles.

    This is intentionally bounded because each highlight requires a drawing
    render. If the rendering API cannot locate the exact handle, leave blank.
    """
    if not reference_path or not compared_path or not findings:return findings
    import re
    native=[]
    for finding in findings:
        if not finding.title.startswith('동일 형상군 원 반경'):
            continue
        ma=re.search(r'^원 핸들 ([0-9A-F]+),',finding.reference)
        mb=re.search(r'^원 핸들 ([0-9A-F]+),',finding.compared)
        if ma and mb:native.append((finding,ma.group(1),mb.group(1)))
    if not native:return findings
    from semantic_pair_upgrade import _highlight_handles
    try:
        a=_highlight_handles(reference_path,[a for _,a,_ in native[:6]])
        b=_highlight_handles(compared_path,[b for _,_,b in native[:6]])
    except Exception:
        return findings
    for finding,ha,hb in native[:6]:
        if a.get(ha) and b.get(hb):
            finding.pdf_box=a[ha]
            finding.dxf_box=b[hb]
            finding.evidence += ' 표시 위치는 해당 CAD 객체 핸들을 직접 렌더링하여 확인했습니다.'
    return findings


def _closed_outlines(doc):
    """Return well-defined simple closed polygon candidates from CAD geometry.

    Uses actual DXF LINE connectivity and closed LWPOLYLINE. Does not treat
    visually touching but unconnected geometry as a closed profile.
    """
    outlines=[]
    for ent in doc.modelspace():
        if ent.dxftype()=='LWPOLYLINE' and bool(ent.closed):
            try:
                pts=[(float(x),float(y)) for x,y,*_ in ent.get_points('xy')]
                if 3<=len(pts)<=16:
                    outlines.append((pts,[str(ent.dxf.handle)]))
            except (TypeError,ValueError,AttributeError):pass
    # Build small cycles from LINE endpoints (exact CAD coordinates to 0.01).
    lines=[];incidence=defaultdict(list)
    for ent in doc.modelspace():
        if ent.dxftype()!='LINE':continue
        try:
            a=(round(ent.dxf.start.x,2),round(ent.dxf.start.y,2))
            b=(round(ent.dxf.end.x,2),round(ent.dxf.end.y,2))
            if a==b:continue
            idx=len(lines);lines.append((a,b,str(ent.dxf.handle)))
            incidence[a].append(idx);incidence[b].append(idx)
        except (AttributeError,ValueError):continue
    # Only components with every vertex of degree two are unambiguous loops.
    visited=set()
    for start in range(len(lines)):
        if start in visited:continue
        todo=[start];component=set();nodes=set()
        while todo:
            idx=todo.pop()
            if idx in component:continue
            component.add(idx)
            a,b,_=lines[idx];nodes|={a,b}
            for p in (a,b):todo.extend(incidence[p])
            if len(component)>1000:break
        visited|=component
        if not (3<=len(component)<=16 and len(nodes)==len(component)):
            continue
        if any(len(incidence[p])!=2 for p in nodes):continue
        first=min(nodes);points=[first];old=None;current=first
        for _ in range(len(component)):
            links=[k for k in incidence[current] if k!=old]
            if len(links)==2 and old is None:
                idx=min(links)
            elif len(links)==1:
                idx=links[0]
            else:
                break
            a,b,_=lines[idx]
            nxt=b if a==current else a
            if nxt==first:
                if len(points)==len(component):
                    outlines.append((points,[lines[k][2] for k in sorted(component)]))
                break
            if nxt in points:break
            points.append(nxt);old=idx;current=nxt
    return [item for item in outlines if _polygon_ok(item[0])]


def _polygon_ok(points):
    area=abs(sum(p[0]*q[1]-q[0]*p[1] for p,q in zip(points,points[1:]+points[:1])))*.5
    return area>1e-4


def _edge_profile(points):
    edges=[(q[0]-p[0],q[1]-p[1]) for p,q in zip(points,points[1:]+points[:1])]
    lengths=[math.hypot(x,y) for x,y in edges]
    scale=max(median(lengths),1e-8)
    angles=[]
    for (x,y),(xx,yy) in zip(edges,edges[1:]+edges[:1]):
        dot=(x*xx+y*yy)/max(1e-12,math.hypot(x,y)*math.hypot(xx,yy))
        angles.append(math.acos(max(-1,min(1,dot))))
    return [v/scale for v in lengths],angles


def _outline_correspondence(a,b):
    if len(a)!=len(b):return None
    al,aa=_edge_profile(a);bl,ba=_edge_profile(b)
    n=len(al);matches=[]
    # Compare cyclic orders in both directions, rotation independent.
    for reverse in (False,True):
        for k in range(n):
            idx=[((k-i) if reverse else (k+i))%n for i in range(n)]
            els=[bl[j] for j in idx];angs=[ba[j] for j in idx]
            angle_error=sum(abs(x-y) for x,y in zip(aa,angs))/n
            side_error=sum(abs(x-y)/max(.01,x,y) for x,y in zip(al,els))/n
            if angle_error<.12 and side_error<.22:
                matches.append((side_error+angle_error,side_error,angle_error))
    if not matches:return None
    return min(matches)


def compare_closed_outline_geometry(ref,check,limit=10):
    """Candidate shape comparison of unique closed line/polyline profiles.

    A rectangle or any repeated/common profile is intentionally left unpaired
    when there is more than one plausible counterpart.
    """
    a=_closed_outlines(ref);b=_closed_outlines(check)
    results=[];used=set()
    for points,handles in a:
        options=[]
        for j,(q,other) in enumerate(b):
            score=_outline_correspondence(points,q)
            if score is not None:options.append((score[0],j,score))
        options.sort()
        if not options or (len(options)>1 and options[1][0]-options[0][0]<.12):continue
        _,j,(_,err_lengths,err_angles)=options[0]
        if j in used:continue
        # Reverse uniqueness: don't pair two similar outlines with one target.
        competitors=[]
        for u,(p,_) in enumerate(a):
            score=_outline_correspondence(p,b[j][0])
            if score is not None:competitors.append((score[0],u))
        competitors.sort()
        if not competitors or competitors[0][1]!=a.index((points,handles)) or (len(competitors)>1 and competitors[1][0]-competitors[0][0]<.12):
            continue
        used.add(j)
        if err_lengths>.045 or err_angles>.065:
            results.append(Finding(0,'shape','확인 필요','닫힌 외곽선 형상비 차이 후보',
                '선분 또는 닫힌 폴리라인의 변 길이 비율과 방향 변화를 이용해 '
                '위치·회전·균일 축척의 영향을 줄였습니다. 동일 부품 외곽인지 검토하세요. '
                '대응 위치가 검증되지 않아 박스를 임의로 표시하지 않습니다.',
                f'기준 윤곽선 CAD 핸들 {handles[:8]}',
                f'검토 윤곽선 CAD 핸들 {b[j][1][:8]}'))
            if len(results)>=limit:break
    return results


def geometry_coverage(reference_doc, compared_doc):
    """Report geometric extraction and confidently paired clusters for diagnostics."""
    a=_rings(reference_doc);b=_rings(compared_doc)
    ga=_groups(a);gb=_groups(b)
    unique=0
    for group in ga:
        sg=_signature(group)
        opts=[other for other in gb if _similar_signature(sg,_signature(other))]
        if len(opts)!=1:continue
        if sum(_similar_signature(_signature(x),_signature(opts[0])) for x in ga)!=1:continue
        if _match_group(group,opts[0]) is not None:unique+=1
    oa=_closed_outlines(reference_doc);ob=_closed_outlines(compared_doc)
    return {
        'circles':(len(a),len(b)),
        'circle_groups':(len(ga),len(gb)),
        'unique_circle_group_pairs':unique,
        'closed_contours':(len(oa),len(ob)),
        'native_dimensions':(len(list(reference_doc.modelspace().query('DIMENSION'))),
                             len(list(compared_doc.modelspace().query('DIMENSION')))),
    }

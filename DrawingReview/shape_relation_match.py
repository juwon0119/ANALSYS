"""Conservative circle topology matcher, independent of DXF sheet translation and scale.

Matches only distinctive circle neighborhoods. It does NOT infer that a circle is
necessarily a physical hole: projected edges and drafting symbols are possible.
"""
from __future__ import annotations
import math
from statistics import median
from difference_review import Finding


def _circles(doc):
    result=[]
    for e in doc.modelspace():
        if e.dxftype() != 'CIRCLE':
            continue
        center=e.dxf.center
        r=float(e.dxf.radius)
        if r>0:
            result.append((float(center.x),float(center.y),r,str(e.dxf.handle)))
    return result


def _neighborhoods(circles):
    """Cluster near circle centers without using absolute world coordinates.

    Only center-based clusters with >=3 distinct centers are eligible. A nearest-
    neighbor distance ratio limits accidental links across distant views.
    """
    if not circles:return []
    centers=sorted(set((round(x,5),round(y,5)) for x,y,_,_ in circles))
    if len(centers)<3:return []
    nearest=[]
    for x,y in centers:
        dd=sorted(math.hypot(x-u,y-v) for u,v in centers if (u,v)!=(x,y))
        if dd:nearest.append(dd[0])
    if not nearest:return []
    step=max(median(nearest),1e-8)
    cutoff=step*2.5
    remaining=set(centers); groups=[]
    while remaining:
        start=remaining.pop(); group={start}; frontier=[start]
        while frontier:
            x,y=frontier.pop()
            neighbors={p for p in remaining if math.hypot(x-p[0],y-p[1])<=cutoff}
            remaining-=neighbors;group|=neighbors;frontier.extend(neighbors)
        if len(group)>=3:
            members=[c for c in circles if (round(c[0],5),round(c[1],5)) in group]
            groups.append(members)
    return groups


def _signature(group):
    pts=list({(round(c[0],5),round(c[1],5)) for c in group})
    dd=sorted(math.hypot(a[0]-b[0],a[1]-b[1]) for i,a in enumerate(pts) for b in pts[i+1:])
    scale=median(dd) if dd else 1
    if scale < 1e-8: return None
    return (len(pts),len(group), tuple(round(d/scale,2) for d in dd[:14]),scale)


def _center_sig(circle,group,scale):
    x,y,r,_=circle
    dist=sorted(math.hypot(x-u,y-v)/scale for u,v,_,_ in group
                if math.hypot(x-u,y-v)>1e-6)
    return tuple(round(v,2) for v in dist[:5])


def compare_circle_topology(ref,check):
    """Return candidates only when geometric relationships uniquely identify a pair.

    Uses local distance ratios, never raw sheet positions. Deliberately provides
    no on-page overlay if renderer bounding box cannot be grounded reliably.
    """
    left=_neighborhoods(_circles(ref));right=_neighborhoods(_circles(check))
    signatures_left=[_signature(g) for g in left]
    signatures_right=[_signature(g) for g in right]
    findings=[]
    for i,sg in enumerate(signatures_left):
        if sg is None:continue
        # topology matches across uniform scaling; require an unambiguous group
        pairs=[j for j,t in enumerate(signatures_right) if t and sg[:3]==t[:3]]
        if len(pairs)!=1 or sum(bool(x and x[:3]==sg[:3]) for x in signatures_left)!=1:continue
        j=pairs[0];a=left[i];b=right[j];ra=sg[3];rb=signatures_right[j][3]
        for ca in a:
            sa=_center_sig(ca,a,ra)
            aa=[x for x in a if _center_sig(x,a,ra)==sa]
            bb=[x for x in b if _center_sig(x,b,rb)==sa]
            if len(aa)!=1 or len(bb)!=1:continue
            cb=bb[0]
            ratio_a=ca[2]/ra;ratio_b=cb[2]/rb
            if abs(ratio_a-ratio_b)>max(0.015,0.06*max(ratio_a,ratio_b)):
                findings.append(Finding(0,'shape','확인 필요','원형 요소 반경비 차이 후보',
                    '서로 이동·균일 확대된 투상도에서도 유지되는 원 중심 간 거리비로 대응 후보를 찾았습니다. '
                    '반경비가 다르지만 도면 기호/실제 홀 여부와 치수 근거는 검토자가 확인해야 합니다. '
                    '좌표 매핑을 검증하지 않았으므로 도면에 임의의 박스를 표시하지 않습니다.',
                    f'기준 원형 객체 {ca[3]} · 반경비 {ratio_a:.4f}',
                    f'검토 원형 객체 {cb[3]} · 반경비 {ratio_b:.4f}'))
            if len(findings)>=10:return findings
    return findings

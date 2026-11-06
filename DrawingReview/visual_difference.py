"""PDF/DXF visual geometry comparison with evidence-oriented localization.

Text and dimension glyphs are *not* geometric differences. The extraction of
annotations is handled separately in difference_review.py. This module only
reports persistent non-text stroke differences after a validated registration.
It is intentionally conservative: missing candidates are not proof of equality.
"""
from __future__ import annotations
from pathlib import Path


def _bounded_box(x, y, w, h, shape, pad=8):
    H, W = shape[:2]
    return (max(0., (x-pad)/W), max(0., (y-pad)/H),
            min(1., (x+w+pad)/W), min(1., (y+h+pad)/H))


def find_visual_candidates(pdf_path: Path, dxf_path: Path, *, max_candidates=12):
    try:
        import cv2
        import numpy as np
        import pymupdf
        from drawing_preview import render_image
    except ImportError as exc:
        return [], f'시각 비교 의존성 부족: {exc}. Setup.cmd를 실행하세요.'
    try:
        pdf_full=render_image(Path(pdf_path),False).convert('L')
        dxf_full=render_image(Path(dxf_path),True).convert('L')
        shrink=min(1., 1100/max(*pdf_full.size,*dxf_full.size))
        def reduce(im):
            a=np.asarray(im,dtype=np.uint8)
            return cv2.resize(a,None,fx=shrink,fy=shrink,interpolation=cv2.INTER_AREA) if shrink<1 else a
        pdf,dxf=reduce(pdf_full),reduce(dxf_full)
        kp1,de1=cv2.ORB_create(nfeatures=5500,fastThreshold=12).detectAndCompute(pdf,None)
        kp2,de2=cv2.ORB_create(nfeatures=5500,fastThreshold=12).detectAndCompute(dxf,None)
        if de1 is None or de2 is None:
            return [],'자동 정렬 기준점을 찾지 못했습니다. 수동으로 도면 위치를 지정하세요.'
        matches=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(de2,de1,k=2)
        good=[m for pair in matches if len(pair)==2 for m,n in [pair] if m.distance<.70*n.distance]
        if len(good)<55:
            return [],f'정렬 근거 부족({len(good)}점). 임의 위치에 표시하지 않습니다.'
        a=np.float32([kp2[m.queryIdx].pt for m in good]);b=np.float32([kp1[m.trainIdx].pt for m in good])
        M,inliers=cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.8)
        if M is None or inliers is None:
            return [],'PDF·DXF 좌표 정렬에 실패하여 위치 표시를 보류했습니다.'
        mask=inliers.ravel().astype(bool); support=int(mask.sum())
        transformed=cv2.transform(a[mask][None,:,:],M)[0]
        errors=np.linalg.norm(transformed-b[mask],axis=1)
        median_err=float(np.median(errors))
        a0,b0=float(M[0,0]),float(M[0,1]); s=(a0*a0+b0*b0)**.5
        if support<65 or support/max(1,len(good))<.57 or median_err>1.9 or not .70<=s<=1.40:
            return [],(f'정렬 정확도 불충분(일치 {support}/{len(good)}, 오차 {median_err:.1f}px). '
                        '임의의 차이 위치 표시는 생략했습니다.')
        H,W=pdf.shape
        aligned=cv2.warpAffine(dxf,M,(W,H),flags=cv2.INTER_LINEAR,borderValue=255)
        # Mask every searchable PDF word (including dimension symbols and text
        # labels), rather than accidentally calling font rasterisation a shape.
        text_mask=np.zeros((H,W),dtype=np.uint8)
        with pymupdf.open(pdf_path) as doc:
            p=doc[0]; sx=W/p.rect.width;sy=H/p.rect.height
            for x0,y0,x1,y1,*_ in p.get_text('words'):
                x0=int(x0*sx)-5;y0=int(y0*sy)-5
                x1=int(x1*sx)+5;y1=int(y1*sy)+5
                cv2.rectangle(text_mask,(max(0,x0),max(0,y0)),
                              (min(W-1,x1),min(H-1,y1)),255,-1)
        # Text-free drawings can still have title block vector strokes; crop
        # the paper frame and lower note/title block instead of matching them.
        roi=np.zeros((H,W),dtype=np.uint8)
        roi[int(H*.11):int(H*.77),int(W*.11):int(W*.86)]=255
        roi=cv2.bitwise_and(roi,cv2.bitwise_not(text_mask))
        # Two-way comparison, tolerant to a small change in line weight.
        ref=cv2.threshold(pdf,155,255,cv2.THRESH_BINARY_INV)[1]
        qry=cv2.threshold(aligned,155,255,cv2.THRESH_BINARY_INV)[1]
        tol=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5))
        only_ref=cv2.bitwise_and(ref,cv2.bitwise_not(cv2.dilate(qry,tol)))
        only_qry=cv2.bitwise_and(qry,cv2.bitwise_not(cv2.dilate(ref,tol)))
        unmatched=cv2.bitwise_or(only_ref,only_qry)
        unmatched=cv2.bitwise_and(unmatched,roi)
        joined=cv2.morphologyEx(unmatched,cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_RECT,(9,7)))
        n, labels,stats,_=cv2.connectedComponentsWithStats(joined,8)
        inverse=cv2.invertAffineTransform(M)
        results=[]
        for x,y,w,h,area in stats[1:]:
            x,y,w,h,area=map(int,(x,y,w,h,area))
            if area<55 or w<10 or h<9 or w>W*.21 or h>H*.20:
                continue
            roi_ref=only_ref[y:y+h,x:x+w]
            roi_qry=only_qry[y:y+h,x:x+w]
            nr=cv2.countNonZero(roi_ref);nd=cv2.countNonZero(roi_qry)
            if max(nr,nd)<45:continue
            # Require meaningful unmatched geometry rather than only a couple
            # of line-weight pixels, near-blank speckles, or tiny glyph remnants.
            if max(nr,nd)/max(1,w*h)<.025: continue
            quad=np.array([[x,y],[x+w,y],[x+w,y+h],[x,y+h]],np.float32)
            back=cv2.transform(quad[None,:,:],inverse)[0]
            minx,miny=np.min(back,axis=0);maxx,maxy=np.max(back,axis=0)
            if maxx<0 or maxy<0 or minx>=dxf.shape[1] or miny>=dxf.shape[0]:continue
            pbox=_bounded_box(x,y,w,h,pdf.shape)
            dbox=_bounded_box(float(minx),float(miny),float(maxx-minx),float(maxy-miny),dxf.shape)
            if dbox[2]<=dbox[0] or dbox[3]<=dbox[1]:continue
            results.append(dict(pdf_box=pbox,dxf_box=dbox,pixels=nr+nd,area=area,
                                evidence='비문자 선분 차이 후보 (PDF/DXF 인쇄 선 비교)'))
        results.sort(key=lambda v:v['pixels'],reverse=True)
        chosen=[]
        for item in results:
            box=item['pdf_box'];cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
            if any(abs(cx-(t['pdf_box'][0]+t['pdf_box'][2])/2)<.028 and
                   abs(cy-(t['pdf_box'][1]+t['pdf_box'][3])/2)<.028 for t in chosen):
                continue
            chosen.append(item)
            if len(chosen)>=max_candidates:break
        msg=(f'정렬 대응점 {support}/{len(good)}, 중간 오차 {median_err:.2f}px. '
             f'문자·주기·표제란 제외 후 비문자 형상 차이 후보 {len(chosen)}곳. '
             '후보는 검토자 확인 전까지 설계 오류가 아닙니다.')
        return chosen,msg
    except Exception as exc:
        return [],f'시각 비교 실패: {type(exc).__name__}: {exc}'


def register_annotation_boxes(pdf_path, dxf_path, boxes):
    """Project verified PDF note regions into a rendered DXF sheet.

    Returns a list of mapped 0..1 rectangles (or None) and diagnostic text.
    This is *registration-based estimated location*, not an independently
    recognized DXF glyph. If registration is uncertain, nothing is mapped.
    """
    if not boxes:
        return [], '표시할 PDF 영역 없음'
    try:
        import cv2
        import numpy as np
        from drawing_preview import render_image
        ref_full = render_image(pdf_path, False).convert('L')
        qry_full = render_image(dxf_path, True).convert('L')
        factor = min(1.0, 1300 / max(*ref_full.size, *qry_full.size))
        def small(im):
            raw = np.asarray(im, dtype=np.uint8)
            if factor < 1.0:
                return cv2.resize(raw, (max(1,int(raw.shape[1]*factor)),
                                        max(1,int(raw.shape[0]*factor))),
                                  interpolation=cv2.INTER_AREA)
            return raw
        ref, qry = small(ref_full), small(qry_full)
        orb = cv2.ORB_create(nfeatures=5200, fastThreshold=12)
        k1, d1 = orb.detectAndCompute(ref, None)
        k2, d2 = orb.detectAndCompute(qry, None)
        if d1 is None or d2 is None:
            return [None]*len(boxes), '도면 정렬 특징점 부족'
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d2, d1, k=2)
        good = [m for pair in pairs if len(pair) == 2 for m,n in [pair]
                if m.distance < 0.68*n.distance]
        if len(good) < 65:
            return [None]*len(boxes), f'도면 대응점 부족({len(good)}개)'
        pts_d = np.float32([k2[m.queryIdx].pt for m in good])
        pts_p = np.float32([k1[m.trainIdx].pt for m in good])
        mat, inliers = cv2.estimateAffinePartial2D(
            pts_d, pts_p, method=cv2.RANSAC, ransacReprojThreshold=2.7)
        if mat is None or inliers is None:
            return [None]*len(boxes), '정렬 행렬 계산 실패'
        mask = inliers.ravel().astype(bool)
        support = int(mask.sum())
        error = np.linalg.norm(cv2.transform(pts_d[mask][None],mat)[0]-pts_p[mask],axis=1)
        median = float(np.median(error)) if support else 1e3
        scale = float(np.hypot(mat[0,0], mat[0,1]))
        if support < 75 or support/len(good) < .58 or median > 1.55 or not .75 <= scale <= 1.35:
            return [None]*len(boxes), f'정렬 신뢰도 부족({support}점, 중간오차 {median:.2f}px)'
        inverse = cv2.invertAffineTransform(mat)
        H,W=ref.shape
        QH,QW=qry.shape
        mapped=[]
        for box in boxes:
            if box is None:
                mapped.append(None);continue
            x0,y0,x1,y1=box
            if not (0<=x0<x1<=1 and 0<=y0<y1<=1):
                mapped.append(None);continue
            points=np.array([[[x0*W,y0*H],[x1*W,y0*H],[x1*W,y1*H],[x0*W,y1*H]]],dtype=np.float32)
            warped=cv2.transform(points,inverse)[0]
            lo=warped.min(axis=0);hi=warped.max(axis=0)
            # Give a little context for differing text lengths while keeping a local box.
            pad_x=max(5.0, (hi[0]-lo[0])*.15)
            pad_y=max(4.0, (hi[1]-lo[1])*.30)
            lx=max(0.0,(lo[0]-pad_x)/QW);rx=min(1.0,(hi[0]+pad_x)/QW)
            ty=max(0.0,(lo[1]-pad_y)/QH);by=min(1.0,(hi[1]+pad_y)/QH)
            if rx<=lx or by<=ty or (rx-lx)>.25 or (by-ty)>.20:
                mapped.append(None)
            else:
                mapped.append((lx,ty,rx,by))
        return mapped, f'검증된 시트 정렬({support}점, 중간오차 {median:.2f}px) · DXF 위치는 정렬 추정 영역'
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        return [None]*len(boxes), f'도면 정렬 사용 불가: {exc}'

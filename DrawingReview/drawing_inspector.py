"""Non-destructive document readability / evidence diagnostics for PDF and DXF.

This is an inventory, not a geometric equivalence test. No OCR is performed,
and a PDF raster drawing never becomes 'machine readable' by pixel inspection.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from pathlib import Path
import json
import re
import ezdxf
import pymupdf


def _clean(s):
    s = str(s or '').replace('%%c','Ø').replace('%%C','Ø').replace('\\P',' ')
    return re.sub(r'\\[A-Za-z][^;]{0,40};', '', s).strip()


def pdf_profile(path):
    file = Path(path)
    result = {"file": str(file), "pages": [], "warnings": [], "size_bytes": file.stat().st_size}
    with pymupdf.open(file) as document:
        result['page_count'] = len(document)
        if not document:
            result['warnings'].append('PDF 페이지가 없습니다.')
        for no, page in enumerate(document):
            blocks = page.get_text('dict')['blocks']
            words = page.get_text('words')
            chars = sum(len(line['spans'][0].get('text','')) if len(line.get('spans',[])) == 1 else sum(len(s.get('text','')) for s in line.get('spans',[])) for b in blocks if b['type']==0 for line in b.get('lines', []))
            drawings = page.get_drawings()
            img_blocks = sum(1 for b in blocks if b['type'] == 1)
            vector_segments = sum(len(d.get('items',[])) for d in drawings)
            # Treat mixed raster/vector as mixed instead of asserting OCR needed.
            mode = '문자·벡터' if chars >= 30 and vector_segments >= 5 else ('문자 중심' if chars >= 30 else ('벡터 중심' if vector_segments >= 5 else ('이미지 중심 / 판독 불충분' if img_blocks else '내용 부족')))
            dim_words = sum(bool(re.search(r'(?:Ø|Φ|DP|THRU|R\d|C\d|M\d|[±])', str(w[4]), re.I)) for w in words)
            result['pages'].append({'page':no+1,'width_pt':round(page.rect.width,2), 'height_pt':round(page.rect.height,2), 'characters':chars, 'words':len(words), 'vector_paths':len(drawings), 'vector_segments':vector_segments, 'image_blocks':img_blocks, 'dimension_tokens':dim_words, 'mode':mode})
            if no==0 and mode in ('이미지 중심 / 판독 불충분','내용 부족'):
                result['warnings'].append('1쪽 PDF에서 추출 가능한 문자/벡터가 적습니다. OCR 또는 수동 영역 판독이 필요합니다. 자동 불일치 확정 금지.')
    return result


def dxf_profile(path):
    file = Path(path)
    doc = ezdxf.readfile(file)
    msp = doc.modelspace()
    types = Counter(); layers = Counter(); text_samples=[]; dim_samples=[]; center_candidates=[]
    xmins=[]; xmaxs=[]; ymins=[]; ymaxs=[]
    for entity in msp:
        typ=entity.dxftype(); layer=entity.dxf.get('layer', '0')
        types[typ]+=1; layers[layer]+=1
        if typ in ('TEXT','MTEXT','ATTRIB') and len(text_samples)<30:
            try: text_samples.append({'type':typ,'layer':layer,'value':_clean(entity.text if typ=='MTEXT' else entity.dxf.text)[:160], 'handle':entity.dxf.handle})
            except Exception: pass
        if typ=='DIMENSION' and len(dim_samples)<30:
            dim_samples.append({'handle':entity.dxf.handle,'layer':layer,'text_override':_clean(entity.dxf.get('text',''))[:160], 'dimtype':int(entity.dxf.get('dimtype',0)) & 7})
        lt = str(entity.dxf.get('linetype','')).upper()
        if len(center_candidates)<100 and ('CENTER' in str(layer).upper() or 'CENTER' in lt or 'CENTRE' in str(layer).upper()):
            center_candidates.append({'handle':entity.dxf.handle,'type':typ,'layer':layer,'linetype':lt})
    return {'file':str(file), 'size_bytes':file.stat().st_size, 'dxf_version':doc.dxfversion, 'model_entities':sum(types.values()), 'entity_types':dict(types.most_common()), 'layers':dict(layers.most_common()),'text_samples':text_samples,'dimension_samples':dim_samples,'centerline_candidates':center_candidates,'warnings': ([] if types.get('DIMENSION',0) else ['DIMENSION 객체가 없습니다. 치수는 분해된 선/문자일 수 있습니다.']) + ([] if center_candidates else ['중심선 후보를 레이어/선종류 이름으로 찾지 못했습니다. 실제 중심선 누락을 의미하지 않습니다.'])}


def inspect(pdf, dxf):
    result={'timestamp':datetime.now().isoformat(timespec='seconds'),'pdf':None,'dxf':None,'warnings':[], 'capabilities':[]}
    for key,filepath,func in (('pdf',pdf,pdf_profile),('dxf',dxf,dxf_profile)):
        try:
            result[key]=func(filepath)
        except Exception as exc:
            result['warnings'].append(f'{key.upper()} 파일 검사 실패: {type(exc).__name__}: {exc}')
    p=result['pdf']; d=result['dxf']
    if p: result['warnings'] += p.get('warnings',[])
    if d: result['warnings'] += d.get('warnings',[])
    if p and d:
        first=p['pages'][0] if p['pages'] else None
        result['capabilities']=[
          {'name':'PDF 표기 문자 추출','status':'가능' if first and first['characters']>=30 else '추가 확인','reason':'텍스트 레이어 기반 추출. 특수기호/분리 표기는 추가 검증 필요'},
          {'name':'DXF 치수 객체 분석','status':'가능' if d['entity_types'].get('DIMENSION',0)>0 else '추가 확인','reason':'DIMENSION 객체 수 기준. 분해된 치수는 별도 판독 필요'},
          {'name':'PDF/DXF 주기 비교','status':'조건부 가능' if first and first['characters']>=30 and (d['entity_types'].get('MTEXT',0)+d['entity_types'].get('TEXT',0)+d['entity_types'].get('DIMENSION',0))>0 else '추가 확인','reason':'동일 문맥의 고유 주기만 자동 대응. 동일 문구 중복 시 수동 확인'},
          {'name':'중심선 누락 자동 확정','status':'추가 확인','reason':'레이어/선종류 후보만 검출. PDF 뷰 투영과 위치 대응을 먼저 확정해야 함'},
          {'name':'전체 형상·공차 일치 확정','status':'추가 확인','reason':'PDF 벡터 도형과 DXF 실측 좌표계의 정합/치수 연결 근거가 필요'},
        ]
    return result


def report(result):
    lines=['[PDF / DXF 도면 판독 환경 점검]',f"검사 시각: {result['timestamp']}",'']
    p=result['pdf']; d=result['dxf']
    if p:
        lines += [f"PDF: {Path(p['file']).name} / {p['page_count']}쪽"]
        for page in p['pages'][:10]:
            lines.append(f"  {page['page']}쪽 · {page['mode']} · 문자 {page['characters']}자 · 벡터 경로 {page['vector_paths']}개 · 이미지 블록 {page['image_blocks']}개 · 치수 후보 {page['dimension_tokens']}개")
    if d:
        lines += ['',f"DXF: {Path(d['file']).name} / {d['dxf_version']}",f"  모델 객체 {d['model_entities']}개, 치수 {d['entity_types'].get('DIMENSION',0)}개, 문자 {d['entity_types'].get('TEXT',0)+d['entity_types'].get('MTEXT',0)}개, 중심선 후보 {len(d['centerline_candidates'])}개",'  주요 객체: '+', '.join(f'{k} {v}' for k,v in list(d['entity_types'].items())[:12]), '  주요 레이어: '+', '.join(f'{k} {v}' for k,v in list(d['layers'].items())[:12])]
    lines += ['','[검토 가능 범위]']
    for item in result['capabilities']:
        lines.append(f"  {item['name']}: {item['status']} — {item['reason']}")
    if result['warnings']:
        lines += ['','[주의 사항]']+[f'  • {warning}' for warning in result['warnings']]
    lines += ['','※ 이 진단은 판독 가능성 확인이며 치수 적합성/형상 일치 판정이 아닙니다.']
    return '\n'.join(lines)


def save_json(result,path):
    Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8-sig')

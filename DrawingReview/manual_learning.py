"""Reviewer-confirmed DXF/DXF discrepancies, scoped by exact source file hashes.

Training here is persistent case memory rather than model fine-tuning.
Only explicit reviewer submissions are saved; previous nominal values are not
injected into unrelated drawings or allowed to authorize edits.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

from difference_review import Finding, CATEGORY


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(db_path):
    db = sqlite3.connect(db_path)
    db.execute('''CREATE TABLE IF NOT EXISTS reviewer_examples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        pdf_sha256 TEXT NOT NULL, dxf_sha256 TEXT NOT NULL,
        pdf_path TEXT NOT NULL, dxf_path TEXT NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, verdict TEXT NOT NULL,
        reference_value TEXT NOT NULL, compared_value TEXT NOT NULL,
        evidence TEXT NOT NULL,
        pdf_box TEXT NOT NULL, dxf_box TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_reviewer_examples_files ON reviewer_examples(pdf_sha256, dxf_sha256)')
    return db


def _validated_box(box):
    if box is None:
        return None
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('위치 정보는 4개 좌표여야 합니다.')
    values = tuple(float(value) for value in box)
    if any(not 0 <= v <= 1 for v in values) or values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError('도면 내 위치 좌표가 올바르지 않습니다.')
    return values


def save_example(db_path, pdf_path, dxf_path, item: Finding, reviewer: str, example_id=None):
    if item.kind not in CATEGORY:
        raise ValueError('허용되지 않은 차이 종류입니다.')
    if not item.title.strip() or not item.evidence.strip():
        raise ValueError('검토 항목과 판정 근거를 입력하세요.')
    if item.verdict not in ('불일치', '확인 필요', '일치'):
        raise ValueError('허용되지 않은 판정입니다.')
    pdf_box = _validated_box(item.pdf_box)
    dxf_box = _validated_box(item.dxf_box)
    if not (pdf_box and dxf_box):
        raise ValueError('기준 DXF와 검토 DXF 양쪽의 대응 위치를 지정한 후 학습 사례를 저장하세요.')
    pdf_path, dxf_path = Path(pdf_path).resolve(), Path(dxf_path).resolve()
    pdf_hash, dxf_hash = fingerprint(pdf_path), fingerprint(dxf_path)
    timestamp = datetime.datetime.now().isoformat(timespec='seconds')
    fields = (timestamp, reviewer.strip() or '미지정', pdf_hash, dxf_hash,
              str(pdf_path), str(dxf_path), item.kind, item.title.strip(), item.verdict,
              item.reference.strip(), item.compared.strip(), item.evidence.strip(),
              json.dumps(pdf_box), json.dumps(dxf_box))
    with _connect(db_path) as db:
        if example_id is not None:
            row = db.execute('SELECT pdf_sha256,dxf_sha256 FROM reviewer_examples WHERE id=?', (example_id,)).fetchone()
            if not row or row != (pdf_hash, dxf_hash):
                raise ValueError('다른 도면의 학습 사례는 이 도면에서 변경할 수 없습니다.')
            db.execute('''UPDATE reviewer_examples SET updated_at=?, reviewer=?, pdf_sha256=?, dxf_sha256=?,
                pdf_path=?, dxf_path=?, kind=?, title=?, verdict=?, reference_value=?, compared_value=?,
                evidence=?, pdf_box=?, dxf_box=? WHERE id=?''', (*fields, example_id))
            return example_id
        cursor = db.execute('''INSERT INTO reviewer_examples (
            created_at,updated_at,reviewer,pdf_sha256,dxf_sha256,pdf_path,dxf_path,kind,title,verdict,
            reference_value,compared_value,evidence,pdf_box,dxf_box)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (timestamp, *fields))
        return cursor.lastrowid


def load_examples(db_path, pdf_path, dxf_path):
    db_path = Path(db_path)
    if not db_path.is_file():
        return []
    pdf_hash, dxf_hash = fingerprint(pdf_path), fingerprint(dxf_path)
    with _connect(db_path) as db:
        rows = db.execute('''SELECT id,kind,title,verdict,reference_value,compared_value,evidence,pdf_box,dxf_box
            FROM reviewer_examples WHERE pdf_sha256=? AND dxf_sha256=? ORDER BY id''',
            (pdf_hash,dxf_hash)).fetchall()
    findings = []
    for case_id,kind,title,verdict,reference,compared,evidence,pdf_box,dxf_box in rows:
        try:
            if kind not in CATEGORY:
                continue
            finding=Finding(0,kind,verdict,title,evidence,reference,compared,
                            _validated_box(json.loads(pdf_box)),_validated_box(json.loads(dxf_box)))
            finding._learn_id = case_id
            findings.append(finding)
        except (ValueError,TypeError,json.JSONDecodeError):
            continue
    return findings


def list_examples(db_path, limit=100):
    if not Path(db_path).is_file():
        return []
    with _connect(db_path) as db:
        return db.execute('''SELECT id,created_at,reviewer,kind,title,verdict,pdf_path,dxf_path
            FROM reviewer_examples ORDER BY id DESC LIMIT ?''', (limit,)).fetchall()


def delete_example(db_path, case_id):
    with _connect(db_path) as db:
        db.execute('DELETE FROM reviewer_examples WHERE id=?', (case_id,))

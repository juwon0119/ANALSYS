"""Interactive training/review window for evidence-based DXF/DXF difference candidates."""
from __future__ import annotations
from pathlib import Path
from collections import Counter
import datetime
import json
import sqlite3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageDraw, ImageFont

from difference_review import CATEGORY, COLOR, Finding, report_text
from manual_learning import save_example, load_examples, list_examples, delete_example


def _store_decision(db_path, pdf, dxf, item, judgment, note, reviewer):
    with sqlite3.connect(db_path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS difference_decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL, reviewer TEXT NOT NULL,
            pdf_path TEXT NOT NULL, dxf_path TEXT NOT NULL,
            kind TEXT NOT NULL, title TEXT NOT NULL, verdict TEXT NOT NULL,
            judgment TEXT NOT NULL, note TEXT NOT NULL,
            reference_text TEXT, compared_text TEXT,
            pdf_box TEXT, dxf_box TEXT)''')
        db.execute('''INSERT INTO difference_decisions
            (recorded_at,reviewer,pdf_path,dxf_path,kind,title,verdict,judgment,note,
             reference_text,compared_text,pdf_box,dxf_box)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                datetime.datetime.now().isoformat(timespec='seconds'),reviewer,str(pdf),str(dxf),
                item.kind,item.title,item.verdict,judgment,note,item.reference,item.compared,
                json.dumps(item.pdf_box),json.dumps(item.dxf_box)))


class PairedDrawingPanel(ttk.Frame):
    """PDF and DXF on the same row, with matching annotation IDs on both images.

    Overlays are normalized to rendered page bounds, never guessed from screen pixels.
    Missing positions intentionally remain unmarked until the user identifies them.
    """
    def __init__(self, parent, left_preview, right_preview):
        super().__init__(parent)
        self.previews = (left_preview, right_preview)
        self.items = ([], [])
        self.active_number = None
        self.photos = [None, None]
        self.show_detail = True  # 선택한 실제 표기 위치 중심으로 확대
        self.active_boxes = (None, None)
        self.mark_side = None
        self.mark_callback = None
        self.mark_origin = None
        self.image_rects = [None, None]
        for i, (title, color) in enumerate((('기준 도면 요소 · DXF', '#A65A1F'),
                                             ('체크할 도면 요소 · DXF', '#146648'))):
            self.grid_columnconfigure(i, weight=1, uniform='paired_drawing')
            host = ttk.Frame(self)
            host.grid(row=0, column=i, sticky='nsew', padx=(0, 5) if i == 0 else (5, 0))
            ttk.Label(host, text=title, foreground=color,
                      font=('Malgun Gothic', 10, 'bold')).pack(anchor='w', pady=(0, 3))
            canvas = tk.Canvas(host, height=220, background='#EDF1F5',
                               highlightthickness=1, highlightbackground='#CFD8E2')
            canvas.pack(fill='both', expand=True)
            canvas.bind('<Configure>', lambda event, index=i: self.redraw(index))
            canvas.bind('<ButtonPress-3>', lambda event, index=i: self._mark_press(event, index))
            canvas.bind('<B3-Motion>', lambda event, index=i: self._mark_move(event, index))
            canvas.bind('<ButtonRelease-3>', lambda event, index=i: self._mark_release(event, index))
            if i == 0:
                self.left_canvas = canvas
            else:
                self.right_canvas = canvas

    def set_findings(self, findings, active_number=None):
        self.active_number = active_number
        active = next((f for f in findings if f.number == active_number), None)
        self.active_boxes = ((active.pdf_box, active.dxf_box) if active else (None, None))
        self.items = (
            [(f.number, f.kind, f.pdf_box) for f in findings if f.pdf_box],
            [(f.number, f.kind, f.dxf_box) for f in findings if f.dxf_box],
        )
        self.redraw(0)
        self.redraw(1)

    def redraw(self, index):
        canvas = (self.left_canvas, self.right_canvas)[index]
        canvas.delete('all')
        self.image_rects[index] = None
        width, height = max(canvas.winfo_width(), 10), max(canvas.winfo_height(), 10)
        source = self.previews[index].image
        if source is None:
            canvas.create_text(width / 2, height / 2, text='도면을 불러오지 못했습니다.',
                               fill='#59697A')
            return
        iw, ih = source.size
        focus = self.active_boxes[index] if self.show_detail else None
        if focus:
            # 도면의 실제 좌표로 판독한 위치를 기준으로 주변 컨텍스트를 확대.
            # 좌표를 얻지 못했을 때 임의 중심으로 점프하지 않는다.
            cx=(focus[0]+focus[2])*0.5
            cy=(focus[1]+focus[3])*0.5
            wnorm=max(.16, (focus[2]-focus[0])*5)
            hnorm=max(.22, (focus[3]-focus[1])*6)
            # 출력 비율과 화면 비율을 고려하여 치수 주변 형상도 함께 나타나게 한다.
            wnorm=min(1., wnorm)
            hnorm=min(1., hnorm)
            crop_left=max(0.,min(1.-wnorm,cx-wnorm/2))
            crop_top=max(0.,min(1.-hnorm,cy-hnorm/2))
            crop_right=crop_left+wnorm
            crop_bottom=crop_top+hnorm
        else:
            crop_left,crop_top,crop_right,crop_bottom=0.,0.,1.,1.
        region = source.crop((int(crop_left*iw),int(crop_top*ih),
                              max(int(crop_left*iw)+1,int(crop_right*iw)),
                              max(int(crop_top*ih)+1,int(crop_bottom*ih))))
        rw0,rh0=region.size
        scale=min((width-14)/rw0,(height-14)/rh0)
        rw,rh=max(1,int(rw0*scale)),max(1,int(rh0*scale))
        left,top=(width-rw)//2,(height-rh)//2
        image=region.resize((rw,rh),Image.Resampling.LANCZOS)
        self.photos[index]=ImageTk.PhotoImage(image)
        canvas.create_image(left,top,anchor='nw',image=self.photos[index])
        self.image_rects[index] = (left, top, rw, rh, crop_left, crop_top, crop_right, crop_bottom)
        if self.mark_side == index:
            canvas.configure(cursor='crosshair')
        for number,kind,bb in self.items[index]:
            if len(bb)!=4 or (focus and number!=self.active_number):
                continue
            x0,y0,x1,y1=bb
            x0,x1=sorted((left+(x0-crop_left)/(crop_right-crop_left)*rw,
                          left+(x1-crop_left)/(crop_right-crop_left)*rw))
            y0,y1=sorted((top+(y0-crop_top)/(crop_bottom-crop_top)*rh,
                          top+(y1-crop_top)/(crop_bottom-crop_top)*rh))
            active=(number==self.active_number)
            pad=5 if active else 3
            color=COLOR.get(kind,'#477BE4')
            kwargs=dict(outline=color,width=3 if active else 2)
            if not active:kwargs['dash']=(5,3)
            method=canvas.create_oval if kind=='center' else canvas.create_rectangle
            method(x0-pad,y0-pad,x1+pad,y1+pad,**kwargs)
            canvas.create_text(max(6,x0-1),max(12,y0-4),text=f'#{number}',
                               fill=color,font=('Malgun Gothic',9,'bold' if active else 'normal'),
                               anchor='sw')
        if focus:
            canvas.create_text(8,10,anchor='nw',text='선택 위치 확대 · 원본 좌표 기준',
                               fill='#365578',font=('Malgun Gothic',8))
        elif not self.items[index]:
            canvas.create_text(width/2,height-10,text='자동으로 확인된 위치 없음 · 직접 지정 가능',
                               fill='#526578',font=('Malgun Gothic',8))


    def enable_mark(self, index, callback):
        """Arm marking on the comparison dialog itself; no main-window navigation."""
        self.mark_side = index if callback is not None else None
        self.mark_callback = callback
        self.mark_origin = None
        for i, canvas in enumerate((self.left_canvas, self.right_canvas)):
            canvas.delete('mark_draft')
            canvas.configure(cursor='crosshair' if i == self.mark_side else '')

    def _mark_press(self, event, index):
        if self.mark_side != index or self.image_rects[index] is None:
            return
        self.mark_origin = (event.x, event.y)
        return 'break'

    def _mark_move(self, event, index):
        if self.mark_side != index or self.mark_origin is None:
            return
        canvas=(self.left_canvas,self.right_canvas)[index]
        canvas.delete('mark_draft')
        x0,y0=self.mark_origin
        canvas.create_rectangle(x0,y0,event.x,event.y,outline='#0A8A86',
                                dash=(5,3),width=2,tags='mark_draft')
        return 'break'

    def _mark_release(self, event, index):
        if self.mark_side != index or self.mark_origin is None:
            return
        canvas=(self.left_canvas,self.right_canvas)[index]
        canvas.delete('mark_draft')
        x0,y0=self.mark_origin
        self.mark_origin=None
        if abs(event.x-x0)<6 or abs(event.y-y0)<6:
            return 'break'
        rect=self.image_rects[index]
        if rect is None:
            return 'break'
        left,top,width,height,u0,v0,u1,v1=rect
        # Reject drags outside of the rendered drawing, rather than silently
        # pinning a mark on an unrelated edge of the image.
        xa,xb=sorted((x0,event.x));ya,yb=sorted((y0,event.y))
        if xa<left or xb>left+width or ya<top or yb>top+height:
            return 'break'
        bb=(u0+(xa-left)/width*(u1-u0),
            v0+(ya-top)/height*(v1-v0),
            u0+(xb-left)/width*(u1-u0),
            v0+(yb-top)/height*(v1-v0))
        callback=self.mark_callback
        self.enable_mark(None,None)
        if callback:
            callback(bb)
        return 'break'


class DifferenceDialog(tk.Toplevel):
    def __init__(self, parent, findings, pdf_path, dxf_path, pdf_preview, dxf_preview, reviewer='', on_report=None):
        super().__init__(parent)
        self.title('도면 차이 종합 검토 · 위치별 표시 · 판단 기록')
        window_w=min(1080,max(760,self.winfo_screenwidth()-110))
        window_h=min(850,max(620,self.winfo_screenheight()-90))
        self.geometry(f'{window_w}x{window_h}')
        self.minsize(760,600)
        self.findings = findings
        self.pdf_path=Path(pdf_path)
        self.dxf_path=Path(dxf_path)
        self.pdf_preview=pdf_preview
        self.dxf_preview=dxf_preview
        self.reviewer=reviewer or '미지정'
        self.on_report=on_report
        self.db_path=self.pdf_path.resolve().parent.parent.parent/'difference_decisions.sqlite3'
        # The database is stored next to application source, not inside drawings.
        self.db_path=Path(__file__).resolve().parent/'difference_decisions.sqlite3'
        self.learning_db_path=Path(__file__).resolve().parent/'reviewer_learning.sqlite3'
        self._automatic_findings=list(findings)
        self.reload_learning_examples(initial=True)
        counts=Counter(f.verdict for f in self.findings)
        header=ttk.Frame(self, padding=(14,11))
        header.pack(fill='x')
        ttk.Label(header,text='도면 차이 종합 검토',font=('Malgun Gothic',14,'bold')).pack(side='left')
        ttk.Label(header,text=f"표기 불일치 {counts['불일치']}건 · 확인 필요 {counts['확인 필요']}건",
                  foreground='#9A4109',font=('Malgun Gothic',10,'bold')).pack(side='right')
        legend=ttk.Frame(self,padding=(12,2))
        legend.pack(fill='x')
        for idx,(color,label) in enumerate([('#477BE4','○ 중심선 후보'),('#DA3943','□ 지름/치수 차이'),
                            ('#477BE4','□ 형상 후보'),('#129764','□ 공차 차이'),
                            ('#B65D14','□ 텍스트 차이')]):
            ttk.Label(legend,text=label,foreground=color).grid(row=idx//3,column=idx%3,
                                        sticky='w',padx=(0,16),pady=1)
        for col in range(3):legend.grid_columnconfigure(col,weight=1)
        zoom_row=ttk.Frame(self,padding=(13,3))
        zoom_row.pack(fill='x')
        ttk.Label(zoom_row,text='항목 선택 → 양쪽 도면에서 대응 위치 비교 · 직접 표시 시 오른쪽 드래그',
                  foreground='#536579').pack(side='left')
        self.detail_toggle=ttk.Button(zoom_row,text='도면 전체 보기',command=self.toggle_detail)
        self.detail_toggle.pack(side='right')
        self.pair_panel = PairedDrawingPanel(self, self.pdf_preview, self.dxf_preview)
        self.pair_panel.pack(fill='x', padx=13, pady=(1,5))
        self.mark_status=tk.StringVar(value='직접 연결: 항목 선택 → 양쪽 도면 오른쪽 드래그 → 비교표 즉시 갱신 → 값 확인·수정 → 학습 저장')
        ttk.Label(self,textvariable=self.mark_status, padding=(13,3),
                  foreground='#2B6675').pack(anchor='w')
        table=ttk.Frame(self,padding=(12,0,12,0))
        table.pack(fill='both',expand=True)
        columns=('num','kind','verdict','title','reference','compared')
        self.tree=ttk.Treeview(table, columns=columns, show='headings', selectmode='browse',height=8)
        heads={'num':'#','kind':'구분','verdict':'판정','title':'검토 항목','reference':'기준 DXF','compared':'검토 DXF'}
        widths={'num':35,'kind':78,'verdict':94,'title':235,'reference':240,'compared':240}
        for col in columns:
            self.tree.heading(col,text=heads[col])
            self.tree.column(col,width=widths[col],minwidth=30,stretch=(col in ('title','reference','compared')))
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        xscroll=ttk.Scrollbar(table,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set,xscrollcommand=xscroll.set)
        table.grid_rowconfigure(0,weight=1)
        table.grid_columnconfigure(0,weight=1)
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll.grid(row=0,column=1,sticky='ns')
        xscroll.grid(row=1,column=0,sticky='ew')
        self.tree.tag_configure('불일치',foreground='#B4232E')
        self.tree.tag_configure('확인 필요',foreground='#344B68')
        for f in self.findings:
            self.tree.insert('', 'end', iid=str(f.number), values=self._row_values(f), tags=(f.verdict,))
        ttk.Label(self,text='근거 및 추가 확인 절차 · 표의 [직접 연결]은 위치 확정이며 실제 치수 판독을 의미하지 않습니다',padding=(12,8,12,0),font=('Malgun Gothic',10,'bold')).pack(anchor='w')
        self.detail=tk.Text(self,height=4,wrap='word',font=('Malgun Gothic',9),
                            background='#F4F7FA',relief='flat',padx=12,pady=9)
        self.detail.pack(fill='x',padx=12,pady=(3,6))
        self.detail.configure(state='disabled')
        buttons=ttk.Frame(self,padding=(12,2,12,12))
        buttons.pack(fill='x')
        actions=[('기준 DXF에 직접 표시',lambda:self.start_mark('pdf')),
                 ('검토 DXF에 직접 표시',lambda:self.start_mark('dxf')),
                 ('검토 판단 저장',self.save_decision),
                 ('차이 직접 추가',self.add_manual_finding),
                 ('텍스트 보고서',self.export_report),
                 ('비교 이미지 PNG 저장',self.export_annotated_image),
                 ('학습 사례 저장',self.save_learning_example),
                 ('학습 사례 관리',self.manage_learning_examples),
                 ('항목 내용 수정',self.edit_selected_finding),
                 ('연결 해제 · 다시 지정',self.unlink_selected_finding),
                 ('비교표 값 수정',self.edit_selected_finding)]
        for col in range(4):
            buttons.grid_columnconfigure(col,weight=1,uniform='compare_actions')
        for index,(label,cb) in enumerate(actions):
            ttk.Button(buttons,text=label,command=cb).grid(
                row=index//4,column=index%4,sticky='ew',padx=(0,6),pady=(0,4))
        ttk.Button(buttons,text='닫기',command=self.close).grid(
            row=3,column=3,sticky='ew',pady=(0,4))
        ttk.Button(buttons,text='판정 절차 안내',command=self.show_guide).grid(
            row=3,column=0,sticky='ew',padx=(0,6),pady=(0,4))
        ttk.Button(buttons,text='표시 초기화',command=self.clear_annotations).grid(
            row=3,column=1,sticky='ew',padx=(0,6),pady=(0,4))
        ttk.Button(buttons,text='종전 판단 기록',command=self.show_history).grid(
            row=3,column=2,sticky='ew',padx=(0,6),pady=(0,4))
        self.tree.bind('<<TreeviewSelect>>',self.on_select)
        self.tree.bind('<Double-1>', lambda event: self.edit_selected_finding())
        self.protocol('WM_DELETE_WINDOW',self.close)
        if self.findings:
            self.tree.selection_set(str(self.findings[0].number))
            self.tree.focus(str(self.findings[0].number))
            self.on_select()

    def reload_learning_examples(self, initial=False):
        """Reapply only examples tied to the exact DXF/DXF contents."""
        old_selection = None if initial else self.selected()
        try:
            learned = load_examples(self.learning_db_path,self.pdf_path,self.dxf_path)
        except (OSError,sqlite3.Error,ValueError) as error:
            learned=[]
            if not initial:
                messagebox.showwarning('학습 사례',f'학습 사례를 불러올 수 없습니다.\n{error}',parent=self)
        self.findings=list(self._automatic_findings)
        for finding in learned:
            finding.number=max((item.number for item in self.findings),default=0)+1
            self.findings.append(finding)
        if not initial:
            self.refresh_findings(selection_id=getattr(old_selection,'_learn_id',None))

    @staticmethod
    def _display_side(value, box, manually_marked=False):
        """Do not imply that choosing a rectangle measured a dimension value."""
        shown = str(value or '').strip()
        if manually_marked and box:
            return ((shown + '  [직접 연결]') if shown else '[직접 연결 · 값 확인 필요]')[:180]
        if box:
            return (shown or '[위치 확인됨 · 값 확인 필요]')[:180]
        return (shown or '[연결 위치 없음]')[:180]

    def _row_values(self, f):
        marks = getattr(f, '_reviewer_marked_sides', set())
        return (f.number, CATEGORY.get(f.kind, f.kind), f.verdict,
                ('[학습] ' if getattr(f, '_learn_id', None) else '') + f.title,
                self._display_side(f.reference, f.pdf_box, 'pdf' in marks),
                self._display_side(f.compared, f.dxf_box, 'dxf' in marks))

    def _refresh_row(self, f):
        """Refresh the selected comparison row as soon as a link is changed."""
        iid = str(f.number)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(f), tags=(f.verdict,))
        self.sync_annotations()

    def refresh_findings(self, selection_id=None, selection_number=None):
        previous = self.selected()
        if selection_number is None and previous is not None:
            selection_number = previous.number
        for row in self.tree.get_children():
            self.tree.delete(row)
        for f in self.findings:
            self.tree.insert('', 'end', iid=str(f.number),
                             values=self._row_values(f), tags=(f.verdict,))
        target = next((f for f in self.findings if getattr(f, '_learn_id', None) == selection_id), None) if selection_id is not None else None
        if target is None and selection_number is not None:
            target = next((f for f in self.findings if f.number == selection_number), None)
        if target is None and self.findings:
            target = self.findings[-1]
        if target is not None:
            self.tree.selection_set(str(target.number))
            self.tree.see(str(target.number))
            self.on_select()
        else:
            self.sync_annotations()

    def unlink_selected_finding(self):
        """Unlink both overlay locations; a reviewer can redraw the correct pair."""
        item=self.selected()
        if item is None:
            messagebox.showinfo('연결 해제','검토 항목을 선택해 주세요.',parent=self)
            return
        if not messagebox.askyesno('연결 해제',
            '이 항목의 기준/검토 DXF 연결 위치를 모두 해제할까요?\n'
            '이후 양쪽 도면에서 직접 영역을 다시 지정할 수 있습니다.\n'
            '저장된 사례는 [학습 사례 저장] 전까지 변경되지 않습니다.',parent=self):
            return
        item.pdf_box=None
        item.dxf_box=None
        item.dxf_marker=''
        item._reviewer_marked_sides = set()
        self._promote_manual_override(item)
        self._refresh_row(item)
        self.mark_status.set('연결이 해제되어 비교표도 갱신됐습니다. 양쪽 도면에 다시 표시해 주세요.')
        self.on_select()

    def _promote_manual_override(self,item):
        """Avoid showing an obsolete auto candidate next to an edited learning copy."""
        if item in self._automatic_findings:
            self._automatic_findings.remove(item)
        item._manual_added=True

    def edit_selected_finding(self):
        item=self.selected()
        if item is None:
            messagebox.showinfo('항목 수정','검토 항목을 먼저 선택하세요.',parent=self)
            return
        dialog=tk.Toplevel(self)
        dialog.title('검토 항목 직접 수정 · 대응 관계 편집')
        dialog.geometry('520x465')
        dialog.transient(self)
        dialog.grab_set()
        body=ttk.Frame(dialog,padding=14)
        body.pack(fill='both',expand=True)
        keys=list(CATEGORY)
        kind=tk.StringVar(value=item.kind)
        verdict=tk.StringVar(value=item.verdict)
        title=tk.StringVar(value=item.title)
        reference=tk.StringVar(value=item.reference)
        compared=tk.StringVar(value=item.compared)
        ttk.Label(body,text='차이 유형').pack(anchor='w')
        ttk.Combobox(body,state='readonly',textvariable=kind,
            values=keys).pack(fill='x',pady=(0,6))
        ttk.Label(body,text='판정').pack(anchor='w')
        ttk.Combobox(body,state='readonly',textvariable=verdict,
            values=('불일치','확인 필요','일치')).pack(fill='x',pady=(0,6))
        for label,var in [('검토 항목 이름',title),('기준 DXF 값/설명',reference),('검토 DXF 값/설명',compared)]:
            ttk.Label(body,text=label).pack(anchor='w')
            ttk.Entry(body,textvariable=var).pack(fill='x',pady=(0,6))
        ttk.Label(body,text='판단 근거').pack(anchor='w')
        note=tk.Text(body,height=4,wrap='word')
        note.pack(fill='x')
        note.insert('1.0',item.evidence)
        def apply():
            if not title.get().strip() or not note.get('1.0','end-1c').strip():
                messagebox.showwarning('입력 확인','항목 이름과 판단 근거를 입력하세요.',parent=dialog)
                return
            item.kind=kind.get()
            item.verdict=verdict.get()
            item.title=title.get().strip()
            item.reference=reference.get().strip()
            item.compared=compared.get().strip()
            item.evidence=note.get('1.0','end-1c').strip()
            self._promote_manual_override(item)
            dialog.destroy()
            self.refresh_findings(selection_number=item.number)
            self.mark_status.set('차이 유형·판정·기준/검토 값이 비교표에 즉시 반영됐습니다. 학습 사례 저장으로 확정하세요.')
        ttk.Button(body,text='수정 내용 적용',command=apply).pack(anchor='e',pady=(10,0))

    def add_manual_finding(self):
        """Allow a reviewer to add a difference that automatic detection missed."""
        dialog=tk.Toplevel(self)
        dialog.title('차이 직접 추가 · 학습 항목 작성')
        dialog.geometry('530x415')
        dialog.transient(self)
        dialog.grab_set()
        body=ttk.Frame(dialog,padding=15)
        body.pack(fill='both',expand=True)
        category_options=[f'{CATEGORY[k]} ({k})' for k in CATEGORY]
        kind_var=tk.StringVar(value=category_options[1])
        title_var=tk.StringVar()
        verdict_var=tk.StringVar(value='불일치')
        reference_var=tk.StringVar()
        compared_var=tk.StringVar()
        ttk.Label(body,text='프로그램이 놓친 차이를 검토자가 직접 입력합니다.',
                  font=('Malgun Gothic',10,'bold')).pack(anchor='w',pady=(0,8))
        ttk.Label(body,text='차이 유형').pack(anchor='w')
        ttk.Combobox(body,textvariable=kind_var,state='readonly',
                     values=category_options,width=28).pack(fill='x',pady=(0,5))
        ttk.Label(body,text='차이 이름 (필수)').pack(anchor='w',pady=(7,0))
        ttk.Entry(body,textvariable=title_var).pack(fill='x')
        ttk.Label(body,text='기준 DXF 표기 또는 설명').pack(anchor='w',pady=(7,0))
        ttk.Entry(body,textvariable=reference_var).pack(fill='x')
        ttk.Label(body,text='검토 DXF 표기 또는 설명').pack(anchor='w',pady=(7,0))
        ttk.Entry(body,textvariable=compared_var).pack(fill='x')
        ttk.Label(body,text='판정').pack(anchor='w',pady=(7,0))
        ttk.Combobox(body,textvariable=verdict_var,state='readonly',
                     values=('불일치','확인 필요','일치')).pack(fill='x')
        ttk.Label(body,text='판정 근거 (필수)').pack(anchor='w',pady=(7,0))
        note=tk.Text(body,height=3,wrap='word')
        note.pack(fill='x')
        def add():
            title=title_var.get().strip()
            reason=note.get('1.0','end-1c').strip()
            if not title or not reason:
                messagebox.showwarning('필수 항목','차이 이름과 근거를 입력하세요.',parent=dialog)
                return
            number=max((f.number for f in self.findings),default=0)+1
            selected_kind=next((key for key in CATEGORY if kind_var.get()==f'{CATEGORY[key]} ({key})'),None)
            if selected_kind is None:
                messagebox.showwarning('차이 유형','차이 유형을 다시 선택하세요.',parent=dialog)
                return
            finding=Finding(number,selected_kind,verdict_var.get(),title,reason,
                            reference_var.get().strip(),compared_var.get().strip())
            finding._manual_added=True
            self.findings.append(finding)
            self.refresh_findings()
            self.tree.selection_set(str(number))
            self.on_select()
            dialog.destroy()
            messagebox.showinfo('위치 지정',
                '새 차이가 검토 목록에 추가되었습니다.\n'
                '아래 [기준 DXF에 직접 표시]와 [검토 DXF에 직접 표시]를 눌러 비교창 양쪽에 표시한 뒤\n'
                '[학습 사례 저장]을 눌러 주세요.\n'
                '지정·저장을 마치기 전까지는 임시 항목입니다.',parent=self)
        ttk.Button(body,text='차이 항목 추가',command=add).pack(anchor='e',pady=(9,0))

    def save_learning_example(self):
        item=self.selected()
        if not item:
            messagebox.showinfo('학습 사례','차이 항목을 먼저 선택하세요.',parent=self)
            return
        if not (item.pdf_box and item.dxf_box):
            messagebox.showwarning('위치 필요',
                '양쪽 도면의 실제 위치를 지정해야 저장할 수 있습니다.\n'
                '기준 DXF 위치 지정 → DXF 위치 지정 순서로 진행하세요.',parent=self)
            return
        if not messagebox.askyesno('학습 사례 저장',
            f'항목: {item.title}\n기준 DXF와 검토 DXF 대응 위치 및 판정 근거를 저장할까요?\n\n'
            '이 기록은 동일한 파일의 재검토에서만 표시되며, 다른 도면의 값이나 치수를 자동 변경하지 않습니다.',
            parent=self):
            return
        try:
            case_id=save_example(self.learning_db_path,self.pdf_path,self.dxf_path,
                item,self.reviewer,getattr(item,'_learn_id',None))
        except (OSError,sqlite3.Error,ValueError) as error:
            messagebox.showerror('학습 사례 저장 실패',str(error),parent=self)
            return
        # A manual entry should not be duplicated in the current session.
        if getattr(item,'_manual_added',False):
            self._automatic_findings=[f for f in self._automatic_findings if f is not item]
        self.reload_learning_examples()
        messagebox.showinfo('학습 사례 저장',f'학습 사례 #{case_id}가 저장되었습니다.\n'
            '동일한 기준·검토 DXF 내용을 다시 검토할 때 표시됩니다.',parent=self)

    def manage_learning_examples(self):
        popup=tk.Toplevel(self)
        popup.title('검토자 학습 사례 관리')
        popup.geometry('750x410')
        panel=ttk.Frame(popup,padding=12)
        panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='직접 등록한 차이 사례 · 같은 도면에만 재표시',
                  font=('Malgun Gothic',10,'bold')).pack(anchor='w',pady=(0,8))
        tree=ttk.Treeview(panel,columns=('id','time','user','type','title'),show='headings')
        for key,label,width in (('id','#',45),('time','기록일시',165),('user','검토자',75),
                                ('type','유형',80),('title','검토 항목',325)):
            tree.heading(key,text=label)
            tree.column(key,width=width,stretch=(key=='title'))
        tree.pack(fill='both',expand=True)
        def update_rows():
            for row in tree.get_children():tree.delete(row)
            try:
                records=list_examples(self.learning_db_path)
                for id_,when,user,kind,title,verdict,pdf,dxf in records:
                    tree.insert('','end',iid=str(id_),values=(id_,when,user,CATEGORY.get(kind,kind),title))
            except (OSError,sqlite3.Error) as error:
                messagebox.showerror('학습 목록',str(error),parent=popup)
        def remove():
            rows=tree.selection()
            if not rows:return
            id_=int(rows[0])
            if not messagebox.askyesno('학습 사례 삭제',f'학습 사례 #{id_}을(를) 삭제할까요?',parent=popup):return
            try:delete_example(self.learning_db_path,id_)
            except (OSError,sqlite3.Error) as error:
                messagebox.showerror('삭제 실패',str(error),parent=popup)
                return
            update_rows()
            self.reload_learning_examples()
        row=ttk.Frame(panel)
        row.pack(fill='x',pady=(8,0))
        ttk.Button(row,text='선택 사례 삭제',command=remove).pack(side='left')
        ttk.Button(row,text='새로고침',command=update_rows).pack(side='left',padx=6)
        ttk.Button(row,text='닫기',command=popup.destroy).pack(side='right')
        update_rows()

    def toggle_detail(self):
        self.pair_panel.show_detail = not self.pair_panel.show_detail
        self.detail_toggle.configure(text='도면 전체 보기' if self.pair_panel.show_detail else '선택 위치 확대')
        self.pair_panel.redraw(0)
        self.pair_panel.redraw(1)

    def selected(self):
        selection=self.tree.selection()
        if not selection:return None
        number=int(selection[0])
        return next((f for f in self.findings if f.number==number),None)

    def _annotations_for(self,kind):
        out=[]
        for f in self.findings:
            bbox=f.pdf_box if kind=='pdf' else f.dxf_box
            if not bbox:continue
            out.append({'box':bbox,'color':COLOR[f.kind],
                        'shape':'oval' if f.kind=='center' else 'rectangle',
                        'label':f'#{f.number}'})
        return out

    def sync_annotations(self):
        self.pdf_preview.set_review_annotations(self._annotations_for('pdf'))
        self.dxf_preview.set_review_annotations(self._annotations_for('dxf'))
        f=self.selected()
        self.pair_panel.set_findings(self.findings, f.number if f else None)

    def _resolve_dxf_mark(self, f):
        if not f or f.dxf_box or not f.dxf_marker:
            return
        try:
            # The colored-MTEXT rendering is used only for identifying the exact
            # plotted label rectangle, not to alter the source drawing file.
            from drawing_preview import render_image
            image, bbox = render_image(self.dxf_path, True, f.dxf_marker)
            if bbox:
                w,h=image.size
                f.dxf_box=tuple(v/s for v,s in zip(bbox,(w,h,w,h)))
        except Exception:
            pass

    def on_select(self,event=None):
        f=self.selected()
        if not f:return
        self._resolve_dxf_mark(f)
        self.sync_annotations()
        self.detail.configure(state='normal')
        self.detail.delete('1.0','end')
        self.detail.insert('1.0',f.report_line()+
              '\n\n※ 표기 비교만으로 가공 형상과 기하공차의 적합 여부를 확정하지 않습니다.')
        self.detail.configure(state='disabled')
        if f.pdf_box:
            self.pdf_preview.focus_review_annotation({'box':f.pdf_box})
        if f.dxf_box:
            self.dxf_preview.focus_review_annotation({'box':f.dxf_box})

    def start_mark(self,kind):
        f=self.selected()
        if not f:
            messagebox.showinfo('위치 지정','차이 목록에서 항목을 먼저 선택하세요.',parent=self)
            return
        index=0 if kind=='pdf' else 1
        # Full-view marking is less ambiguous than marking a crop that may
        # move as the selected annotation changes. The normalized coordinate
        # transform still supports a zoom crop if the user toggles it.
        self.pair_panel.enable_mark(index,lambda box,side=kind,item=f:self._finish_mark(side,box,item))
        self.mark_status.set(
            f"{'기준 DXF(왼쪽)' if index==0 else '검토 DXF(오른쪽)'} 도면 위에서 오른쪽 마우스 버튼으로 드래그하세요. "
            '완료하면 같은 검토 항목의 위치로 등록됩니다.')

    def _finish_mark(self,kind,box,item=None):
        f=item or self.selected()
        if f not in self.findings:
            return
        if kind=='pdf':
            f.pdf_box=box
        else:
            f.dxf_box=box
            # Do not replace the reviewer-chosen location with an old auto marker.
            f.dxf_marker=''
        f._reviewer_marked_sides = set(getattr(f, '_reviewer_marked_sides', set())) | {kind}
        self._promote_manual_override(f)
        self.tree.selection_set(str(f.number))
        self._refresh_row(f)
        both=bool(f.pdf_box and f.dxf_box)
        self.mark_status.set(
            '양쪽 위치 연결 완료 · 비교표 갱신됨. 실제 치수값은 [항목 내용 수정]에서 확인·입력한 뒤 [학습 사례 저장]을 누르세요.'
            if both else '한쪽 위치가 비교표에 연결됐습니다. 다른 도면에서도 같은 요소를 지정하세요.')
        self.on_select()

    def save_decision(self):
        f=self.selected()
        if not f:
            return
        popup=tk.Toplevel(self)
        popup.title('검토자 판정 등록')
        popup.resizable(False,False)
        popup.transient(self)
        popup.grab_set()
        body=ttk.Frame(popup,padding=16)
        body.pack(fill='both',expand=True)
        ttk.Label(body,text=f'{CATEGORY[f.kind]} · {f.title}',font=('Malgun Gothic',10,'bold')).pack(anchor='w',pady=(0,10))
        decision=tk.StringVar(value='추가 확인 필요')
        for label in ('오류 확정','추가 확인 필요','정상 또는 허용'):
            ttk.Radiobutton(body,text=label,variable=decision,value=label).pack(anchor='w')
        ttk.Label(body,text='판정 근거/조치 이력').pack(anchor='w',pady=(8,2))
        notes=tk.Text(body,width=54,height=5,wrap='word')
        notes.pack()
        def commit():
            note=notes.get('1.0','end-1c').strip()
            if not note:
                messagebox.showwarning('판정 근거 필요','판정 이유나 추가 확인 사항을 입력하세요.',parent=popup)
                return
            try:
                _store_decision(self.db_path,self.pdf_path,self.dxf_path,
                                f,decision.get(),note,self.reviewer)
            except Exception as error:
                messagebox.showerror('판정 저장 실패',str(error),parent=popup)
                return
            popup.destroy()
            messagebox.showinfo('판정 기록','검토 판단을 별도 DB에 저장했습니다.\n이전 값은 자동으로 현재 도면에 적용하지 않습니다.',parent=self)
        ttk.Button(body,text='판정 저장',command=commit).pack(anchor='e',pady=(8,0))

    def show_history(self):
        popup=tk.Toplevel(self)
        popup.title('검토자 판단 기록 · 학습 사례')
        popup.geometry('770x430')
        viewer=tk.Text(popup,wrap='word',font=('Malgun Gothic',9),padx=12,pady=12)
        viewer.pack(side='left',fill='both',expand=True)
        scrollbar=ttk.Scrollbar(popup,orient='vertical',command=viewer.yview)
        scrollbar.pack(side='right',fill='y')
        viewer.configure(yscrollcommand=scrollbar.set)
        try:
            if self.db_path.is_file():
                with sqlite3.connect(self.db_path) as db:
                    rows=db.execute('SELECT recorded_at,reviewer,kind,title,judgment,note,pdf_path,dxf_path FROM difference_decisions ORDER BY id DESC LIMIT 35').fetchall()
            else:
                rows=[]
            for date,reviewer,kind,title,judgment,note,pdf,dxf in rows:
                viewer.insert('end',f'{date} · {reviewer} · {CATEGORY.get(kind,kind)}\n'
                             f'항목: {title}\n판정: {judgment}\n근거: {note}\n'
                             f'기준: {Path(pdf).name} / 검토: {Path(dxf).name}\n\n')
            if not rows:
                viewer.insert('end','저장된 검토 판단 사례가 없습니다.\n검토 목록에서 항목을 선택하고 [검토 판단 저장]을 이용하세요.')
        except sqlite3.Error as error:
            viewer.insert('end',f'판정 기록을 불러오지 못했습니다.\n{error}')
        viewer.configure(state='disabled')

    def export_report(self):
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.txt',
                        initialfile=self.dxf_path.stem+'_difference_review.txt',
                        filetypes=[('텍스트','*.txt')])
        if not path:return
        try:
            Path(path).write_text(report_text(self.findings,self.pdf_path,self.dxf_path),encoding='utf-8-sig')
        except OSError as error:
            messagebox.showerror('보고서 저장 실패',str(error),parent=self)
            return
        messagebox.showinfo('저장 완료','차이 검토 보고서를 저장했습니다.',parent=self)

    def export_annotated_image(self):
        """Export an independently usable side-by-side comparison PNG.

        Only grounded locations (detected or reviewer-marked) are outlined.
        Original PDF and DXF are never modified.
        """
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.png',
                    initialfile=self.dxf_path.stem+'_comparison.png',filetypes=[('PNG 이미지','*.png')])
        if not path:
            return
        try:
            imgs=[self.pdf_preview.image,self.dxf_preview.image]
            if any(img is None for img in imgs):
                raise ValueError('기준 또는 검토 DXF 도면 이미지가 준비되지 않았습니다.')
            cell_width=1100
            header_h=80
            resized=[]
            for img in imgs:
                height=round(img.height*cell_width/img.width)
                resized.append(img.resize((cell_width,height),Image.Resampling.LANCZOS))
            canvas_height=header_h+max(i.height for i in resized)+30
            output=Image.new('RGB',(cell_width*2+36,canvas_height),'#F5F7FA')
            paint=ImageDraw.Draw(output)
            font_path=next((p for p in (
                '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
                '/usr/share/fonts/truetype/nanum/NanumSquareB.ttf',
                'C:/Windows/Fonts/malgun.ttf') if Path(p).is_file()),None)
            font=ImageFont.truetype(font_path,28) if font_path else ImageFont.load_default()
            small=ImageFont.truetype(font_path,18) if font_path else ImageFont.load_default()
            paint.text((18,15),'기준 도면 요소 · DXF',font=font,fill='#9A4109')
            paint.text((cell_width+36,15),'체크할 도면 요소 · DXF',font=font,fill='#146648')
            all_items=self.pair_panel.items
            for side,(img,rows) in enumerate(zip(resized,all_items)):
                ox=18+side*(cell_width+18)
                oy=header_h
                output.paste(img,(ox,oy))
                for number,kind,bb in rows:
                    try:
                        x0,y0,x1,y1=bb
                        box=[ox+min(x0,x1)*img.width-5,oy+min(y0,y1)*img.height-5,
                             ox+max(x0,x1)*img.width+5,oy+max(y0,y1)*img.height+5]
                        rgb=COLOR[kind]
                        if kind=='center':
                            paint.ellipse(box,outline=rgb,width=5)
                        else:
                            paint.rectangle(box,outline=rgb,width=5)
                        paint.text((box[0],max(header_h,box[1]-25)),f'#{number}',font=small,fill=rgb)
                    except (TypeError,ValueError,KeyError):
                        continue
            output.save(path,'PNG')
            messagebox.showinfo('비교 이미지 저장','기준 DXF와 검토 DXF의 대응 위치를 나란히 저장했습니다.',parent=self)
        except Exception as error:
            messagebox.showerror('이미지 저장 실패',str(error),parent=self)

    def show_guide(self):
        text=('1. 기준 DXF와 검토 DXF를 선택하고 모든 치수·단위를 확인합니다.\n\n'
              '2. 동일 형식의 가공 지시를 1:1 매칭하여 지름·깊이·수량 등을 비교합니다.\n\n'
              '3. 두 DXF의 동일 투상도·동일 치수 요소를 대응시켜 표시값을 비교합니다. '
              '실제 STP 형상은 별도의 솔리드 측정이 필요합니다.\n\n'
              '4. 중심선/숨은선과 형상은 레이어, 투영 방향, CAD 객체 및 시각적 근거를 확인합니다. '
              '객체 수나 픽셀 차이만으로 오류를 확정하지 않습니다.\n\n'
              '5. 목록을 선택하고 [차이 직접 추가] 후 비교창 양쪽 도면에서 오른쪽 드래그해 영역을 표시합니다.\n\n'
              '6. 검토자가 오류 확정/추가 확인/정상 여부를 선택하고 근거를 기록합니다. '
              '이전 사례는 판단 절차만 참고하고 치수값은 재사용하지 않습니다.\n\n'
              '표시 색상: 중심선 파란 타원, 지름·치수 빨간 사각형, 형상 파란 사각형, '
              '공차 녹색 사각형, 텍스트 주황색 사각형.')
        messagebox.showinfo('검토 및 학습 절차',text,parent=self)

    def clear_annotations(self):
        self.pdf_preview.set_review_annotations([])
        self.dxf_preview.set_review_annotations([])
        # Clearing the overlays never changes the source drawings or findings.
        self.pair_panel.set_findings([], None)


    def close(self):
        self.pair_panel.enable_mark(None,None)
        self.pdf_preview.enable_region_mark(None)
        self.dxf_preview.enable_region_mark(None)
        self.pdf_preview.set_review_annotations([])
        self.dxf_preview.set_review_annotations([])
        self.destroy()

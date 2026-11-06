from pathlib import Path
import os
import json
import sqlite3
from datetime import date, datetime
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from review_001 import review as review_001
from review_005 import review as review_005
from review_depth import review_depth
from functools import partial

EXERCISES = {"001": (review_001, "C/BØ9.5 HOLE DP", "2E1", "2EC"),
             "002": (partial(review_depth, pdf_prefix='C/BØ17.5 HOLE DP', dxf_prefix='C/B %%c17.5 HOLE DP',
                            label_prefix='17.5 HOLE DP', dim_handle='2DF', label_handle='2EC'),
                     'C/BØ17.5 HOLE DP', '2DF', '2EC'),
             "003": (partial(review_depth, pdf_prefix='C/BØ9.5 HOLE DP', dxf_prefix='C/B %%c9.5 HOLE DP',
                            label_prefix='9.5 HOLE DP', dim_handle='269', label_handle='277'),
                     'C/BØ9.5 HOLE DP', '269', '277'),
             "004": (partial(review_depth, pdf_prefix='3 x M6 TAP DP', dxf_prefix='3 x M6 TAP DP',
                            label_prefix='3 x M6 TAP DP', dim_handle='3BB', label_handle='3C1'),
                     '3 x M6 TAP DP', '3BB', '3C1'),
             "005": (review_005, "C/BØ11 HOLE DP", "33E", "34C")}

base = Path(__file__).resolve().parent

COMPANY_NAME = "대주코레스"
MANAGER_NAMES = ("박주원", "박진수", "배세환")
# 설치 폴더의 쓰기 권한과 관계없이 사용자별 설정에 관리자 명단을 보관한다.
SETTINGS_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DrawingReview"
MANAGER_DATA = SETTINGS_DIR / "managers.json"
DOCUMENT_DATA = SETTINGS_DIR / "document_info.json"


def load_managers():
    names = list(MANAGER_NAMES)
    try:
        data = json.loads(MANAGER_DATA.read_text(encoding="utf-8"))
        if isinstance(data, list):
            cleaned = []
            for item in data:
                if isinstance(item, str) and item.strip() and item.strip() not in cleaned:
                    cleaned.append(item.strip())
            if cleaned:
                names = cleaned  # 저장된 명단을 우선한다. 삭제한 기본 관리자도 복원하지 않는다.
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    return names


def persist_managers(names):
    MANAGER_DATA.parent.mkdir(parents=True, exist_ok=True)
    MANAGER_DATA.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")

def load_documents():
    try:
        data = json.loads(DOCUMENT_DATA.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def persist_documents(documents):
    DOCUMENT_DATA.parent.mkdir(parents=True, exist_ok=True)
    temporary = DOCUMENT_DATA.with_suffix('.tmp')
    temporary.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(DOCUMENT_DATA)


documents = load_documents()

root = tk.Tk()
root.title("도면 검토 통합 실습")
# 화면 해상도에 맞춰 시작 크기를 제한하고, 작업 영역은 필요 시에만 스크롤한다.
screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
window_width = min(1520, max(940, screen_width - 55))
window_height = min(940, max(600, screen_height - 85))
root.geometry(f"{window_width}x{window_height}")

# 두 작업 영역은 서로 독립적으로 크기를 배분하고 스크롤한다.
root.grid_rowconfigure(0, weight=1)
root.grid_columnconfigure(0, weight=43, uniform="main_area")
root.grid_columnconfigure(1, weight=57, uniform="main_area")

left_container = ttk.Frame(root)
left_container.grid(row=0, column=0, sticky="nsew")
left_canvas = tk.Canvas(left_container, highlightthickness=0, borderwidth=0)
left_canvas.pack(side="left", fill="both", expand=True)
left_scroll = ttk.Scrollbar(left_container, orient="vertical", command=left_canvas.yview)
left_scroll.pack(side="right", fill="y")
left_canvas.configure(yscrollcommand=left_scroll.set)
panel = ttk.Frame(left_canvas, padding=(10, 8, 9, 8))
left_window = left_canvas.create_window((0, 0), window=panel, anchor="nw")


def update_left_layout(event=None):
    """내용이 실제 화면보다 커지면 잘라내지 않고 스크롤 영역으로 남긴다."""
    width = max(1, left_canvas.winfo_width())
    left_canvas.itemconfigure(left_window, width=width)
    requested = max(1, panel.winfo_reqheight())
    left_canvas.configure(scrollregion=(0, 0, width, requested))


def scroll_content(event):
    """각 작업영역을 독립 스크롤; 도면 위 마우스 휠은 확대/축소."""
    widget = root.winfo_containing(root.winfo_pointerx(), root.winfo_pointery())
    if widget is None or not event.delta:
        return
    ancestor = widget
    while ancestor is not None:
        if isinstance(ancestor, tk.Text):
            return   # 결과 텍스트에는 해당 텍스트의 고유 스크롤 사용
        if isinstance(ancestor, tk.Canvas) and ancestor is not left_canvas:
            return   # DrawingPreview의 자체 확대/축소 기능 유지
        ancestor = getattr(ancestor, "master", None)
    ancestor = widget
    while ancestor is not None:
        if ancestor == left_container:
            left_canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
            return "break"
        ancestor = getattr(ancestor, "master", None)

left_canvas.bind("<Configure>", update_left_layout)
panel.bind("<Configure>", update_left_layout)
root.bind_all("<MouseWheel>", scroll_content, add="+")

exercise = tk.StringVar(value="001")
pdf = tk.StringVar()  # 기존 변수명 유지: 실제 입력은 기준 DXF 경로
dxf = tk.StringVar()
converted_pdf_path = tk.StringVar(value="")  # 별도로 내보낸 PDF의 경로
corrected_dxf_path = tk.StringVar(value="")  # 하단 2D: 승인 완료된 수정본 DXF만 표시
status = tk.StringVar(value="예제 파일을 선택했습니다. 종합 도면 검토를 실행하세요.")


def pick(variable, suffix):
    path = filedialog.askopenfilename(parent=root, filetypes=[(suffix.upper(), f"*.{suffix}")])
    if path:
        variable.set(path)


def matching_case(current, target):
    database = base / "review_cases.sqlite3"
    if not database.is_file():
        return ""
    _, _, dimension, label = EXERCISES[exercise.get()]
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT reason FROM cases WHERE instr(result, ?) > 0 ORDER BY id DESC LIMIT 1",
            (f"수정 제안: {current} → {target} (DIMENSION {dimension}, MTEXT {label})",),
        ).fetchone()
    return row[0] if row else ""


def reference_case_001():
    database = base / "review_cases.sqlite3"
    if not database.is_file():
        return ""
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT reason FROM cases WHERE instr(result, ?) > 0 ORDER BY id DESC LIMIT 1",
            ("수정 제안: 6.5 → 5.5 (DIMENSION 2E1, MTEXT 2EC)",),
        ).fetchone()
    return row[0] if row else ""


def destination(current, target):
    try:
        note = matching_case(current, target)
        history = f"\n이전 동일 수정 사례: {note[:300]}\n" if note else ""
    except sqlite3.Error:
        history = "\n이전 사례를 읽을 수 없습니다.\n"

    try:
        reference_note = reference_case_001() if exercise.get() == "005" else ""
        if reference_note:
            history += f"\n001번 참고 사례 (판단 절차): {reference_note[:300]}\n"
    except sqlite3.Error:
        history += "\n001번 참고 사례 파일을 읽을 수 없습니다.\n"

    number = exercise.get()
    _, prefix, _, _ = EXERCISES[number]
    if not messagebox.askyesno("수정 승인",
        f"기준 DXF  {prefix} {target}\n검토 DXF  {current} → {target}\n{history}새 DXF로 저장할까요?",
        parent=root, default=messagebox.NO):
        return None
    source = Path(dxf.get()).resolve()
    folder = source.parent
    if folder.name != "결과":
        folder = folder / "결과"
    folder.mkdir(exist_ok=True)
    name = f"{number}_A00_corrected"
    candidate = folder / f"{name}.dxf"
    suffix = 2
    while candidate.exists():
        candidate = folder / f"{name}_{suffix}.dxf"
        suffix += 1
    path = filedialog.asksaveasfilename(parent=root, defaultextension=".dxf",
        initialdir=str(folder),
        initialfile=candidate.name, filetypes=[("DXF", "*.dxf")])
    if path:
        if Path(path).exists():
            raise ValueError("이미 있는 파일입니다. 새 이름을 선택하세요.")
        dxf_output.set(path)
        return Path(path)
    return None


def run(save=False, report_error=True):
    if not save and 'reset_difference_highlights' in globals():
        reset_difference_highlights()
    expected = tuple(status.get().splitlines())

    def propose(current, target):
        nonlocal values
        values = current, target
        if save:
            if tuple(messages) != expected:
                raise ValueError("검토 결과가 달라졌습니다. 다시 분석하세요.")
            return destination(current, target)
        approve_button.configure(state="normal")
        return None

    approve_button.configure(state="disabled")
    result.set("")
    dxf_output.set("")
    messages = []
    values = None
    try:
        review, _, _, _ = EXERCISES[exercise.get()]
        from dxf_reference_support import review_dxf_depth
        _, prefix, _, _ = EXERCISES[exercise.get()]
        review_dxf_depth(Path(pdf.get()), Path(dxf.get()), propose, lambda: True,
                         messages.append, prefix=prefix)
    except Exception as error:
        approve_button.configure(state="disabled")
        status.set(f"깊이 항목 확인 필요: {error}")
        if report_error:
            messagebox.showerror("검토 중 오류", str(error), parent=root)
        return False
    if save and not dxf_output.get() and tuple(messages) == expected:
        approve_button.configure(state="normal")
    status.set("\n".join(messages))
    source_path = dxf.get()
    if dxf_output.get():
        # 원본 검토 DXF는 중간 행에 유지한다. 새 수정본만 하단 미리보기에 띄운다.
        corrected = Path(dxf_output.get()).resolve()
        if corrected.is_file() and corrected.stat().st_size > 0:
            saved_path.set(str(corrected))
            corrected_dxf_path.set(str(corrected))
            if 'refresh_pdf_list' in globals():
                refresh_pdf_list()
        else:
            messagebox.showwarning("수정본 확인", "수정본 DXF 파일이 아직 생성되지 않았습니다.", parent=root)
    if values or (messages and "일치: DP " in messages[0]):
        current, target = values or (messages[0].rsplit(" ", 1)[-1],) * 2
        _, prefix, _, _ = EXERCISES[exercise.get()]
        matched = bool(dxf_output.get()) or current == target
        label_prefix = prefix.split("Ø", 1)[-1]
        pdf_preview.refresh(f"{prefix} {target}", "#C05A00")
        dxf_preview.refresh(f"{label_prefix} {target if matched else current}",
                            "#16804A" if matched else "#D01920")
        if dxf_output.get():
            before_after[:] = [
                (source_path, f"{label_prefix} {current}", "#D01920"),
                (dxf.get(), f"{label_prefix} {target}", "#16804A"),
            ]
            toggle_button.configure(state="normal")
        showing_before.set(False)
        toggle_button.configure(text="수정 전 보기")
    result.set("\n".join([f"실습 번호: {exercise.get()}\n기준 DXF: {Path(pdf.get()).name}",
                           f"검토 DXF: {Path(dxf.get()).name}", *messages]))
    return True


def save_pdf():
    # 수정 승인된 DXF가 있으면 그 파일을 PDF로 내보낸다.
    active_dxf = corrected_dxf_path.get() if corrected_dxf_path.get() and Path(corrected_dxf_path.get()).is_file() else dxf.get()
    path = filedialog.asksaveasfilename(parent=root, defaultextension=".pdf",
        initialfile=Path(active_dxf).stem + "_preview.pdf", filetypes=[("PDF", "*.pdf")])
    if not path:
        return
    try:
        from export_pdf import export_pdf
        export_pdf(Path(active_dxf), Path(path))
    except Exception as error:
        messagebox.showerror("PDF 저장 실패", str(error), parent=root)
        return
    # 실제로 생성된 파일만 하단 미리보기에 표시한다.
    converted_pdf_path.set(str(Path(path).resolve()))
    refresh_pdf_list()
    messagebox.showinfo("PDF 저장", "검토용 PDF를 저장했습니다.", parent=root)


def save_result():
    content = result.get()
    if not content:
        messagebox.showinfo("검토 결과", "먼저 깊이 표기를 검토하세요.", parent=root)
        return
    path = filedialog.asksaveasfilename(parent=root, defaultextension=".txt",
        initialfile=f"{exercise.get()}_A00_review.txt", filetypes=[("텍스트", "*.txt")])
    if path:
        try:
            if Path(path).suffix.lower() != ".txt":
                raise ValueError("텍스트 파일(.txt)로 저장하세요.")
            with Path(path).open("x", encoding="utf-8-sig") as file:
                file.write(content + "\n")
        except (OSError, ValueError) as error:
            messagebox.showerror("결과 저장 실패", str(error), parent=root)
            return
        messagebox.showinfo("검토 결과", "검토 결과를 저장했습니다.", parent=root)


class CaseDecision(simpledialog.Dialog):
    def __init__(self, parent, content):
        self.content = content
        self.result = None
        super().__init__(parent, "검토 판단 선택")

    def body(self, frame):
        ttk.Label(frame, text=self.content, wraplength=540, justify="left").pack(anchor="w")
        self.decision = tk.StringVar()
        options = ("기준 DXF와 일치 확인", "추가 확인 필요") if "일치: DP " in self.content else (
            ("기준 DXF에 맞춰 수정본 저장", "추가 확인 필요") if "두 객체의 저장값 확인 완료:" in self.content else
            ("기준 DXF에 맞춰 수정 제안", "추가 확인 필요", "수정 보류")
        )
        self.decision.set(options[0])
        for option in options:
            ttk.Radiobutton(frame, text=option, value=option,
                            variable=self.decision).pack(anchor="w", pady=2)
        ttk.Label(frame, text="추가 메모 (선택)").pack(anchor="w", pady=(8, 0))
        self.note = ttk.Entry(frame, width=55)
        self.note.pack(fill="x")
        ttk.Label(frame, text="AI 검토 답변 붙여넣기 (선택)").pack(anchor="w", pady=(8, 0))
        self.ai_reply = tk.Text(frame, width=55, height=5, wrap="word")
        self.ai_reply.pack(fill="x")

    def apply(self):
        self.result = self.decision.get()
        note = self.note.get().strip()
        if note:
            self.result += f" · {note}"
        reply = self.ai_reply.get("1.0", "end-1c").strip()
        if reply:
            self.result += f"\n[AI 참고 답변]\n{reply}"


def review_cases(add=False):
    content = result.get()
    reason = None
    if add:
        if not content:
            messagebox.showinfo("검토 사례", "먼저 깊이 표기를 검토하세요.", parent=root)
            return
        reason = CaseDecision(root, content).result
        if not reason or not reason.strip():
            return
    try:
        with sqlite3.connect(base / "review_cases.sqlite3") as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY, result TEXT NOT NULL, reason TEXT NOT NULL,
                UNIQUE(result, reason)
            )""")
            if add:
                db.execute("INSERT OR IGNORE INTO cases(result, reason) VALUES (?, ?)",
                           (content, reason.strip()))
            rows = db.execute("SELECT result, reason FROM cases ORDER BY id DESC LIMIT 20").fetchall()
    except sqlite3.Error as error:
        messagebox.showerror("검토 사례", str(error), parent=root)
        return
    if "refresh_case_preview" in globals():
        refresh_case_preview()
    window = tk.Toplevel(root)
    window.title("이전 검토 사례")
    window.geometry("740x380")
    text = tk.Text(window, wrap="word", padx=12, pady=12)
    text.pack(fill="both", expand=True)
    text.insert("1.0", "\n\n".join(f"{record}\n검토자 판단: {note}" for record, note in rows)
                or "저장된 검토 사례가 없습니다.")
    text.configure(state="disabled")


dxf_output = tk.StringVar()
saved_path = tk.StringVar()


def open_result_folder():
    if not saved_path.get():
        messagebox.showinfo("결과 폴더", "먼저 수정 DXF를 저장하세요.", parent=root)
        return
    try:
        os.startfile(Path(saved_path.get()).parent)
    except OSError as error:
        messagebox.showerror("결과 폴더", str(error), parent=root)
result = tk.StringVar()
for variable in (pdf, dxf):
    variable.trace_add("write", lambda *_: result.set(""))

def select_exercise(event=None):
    number = exercise.get()
    folder = base / "examples" / number
    # 이전 예제는 기준 PDF만 포함한다. DXF를 임의 생성하거나 검토 파일을
    # 기준으로 재사용하지 않는다. 사용자가 실제 기준 DXF를 선택해야 한다.
    references = (folder / 'reference.dxf', folder / f'{number}_A00_reference.dxf')
    found = next((candidate for candidate in references if candidate.is_file()), None)
    pdf.set(str(found) if found else '')
    dxf.set(str(folder / f"{number}_A00_practice.dxf"))
    saved_path.set("")
    status.set(f"{number}번 예제가 선택되었습니다. 기준 DXF를 선택한 뒤 종합 검토하세요.")
    if 'restore_document' in globals() and 'document_summary' in globals():
        restore_document()


root.minsize(840, 540)
style = ttk.Style(root)
style.configure("Step.TLabelframe.Label", font=("Segoe UI", 11, "bold"),
                foreground="#244A80")
style.configure("InfoTitle.TLabel", font=("Segoe UI", 9, "bold"), foreground="#7B2D12")
style.configure("InfoValue.TLabel", font=("Segoe UI", 10, "bold"), foreground="#213547")
style.configure("ManagerChip.TLabel", font=("Segoe UI", 9), foreground="#7A1F20", background="#FFF4F1")
# 작업영역 전체에서 동일한 글꼴·버튼 높이·테두리 간격을 사용한다.
style.configure("Action.TButton", font=("Malgun Gothic", 9), padding=(8, 4))
style.configure("Primary.TButton", font=("Malgun Gothic", 9, "bold"), padding=(10, 5))
style.configure("Compact.TEntry", padding=(5, 3))
style.configure("Compact.TCombobox", padding=(5, 3))
style.configure("CardSmall.TButton", font=("Malgun Gothic", 9), padding=(2, 2))
style.configure("Step.TLabelframe", padding=(8, 5))

header = ttk.Frame(panel)
header.pack(fill="x", pady=(0, 5))
header.columnconfigure(0, weight=1)
header.columnconfigure(1, weight=0)

header_left = ttk.Frame(header)
header_left.grid(row=0, column=0, sticky="nw")
ttk.Label(header_left, text="도면 검토 통합 실습", font=("Segoe UI", 17, "bold")).pack(anchor="w")
ttk.Label(header_left, text="기준 DXF와 검토 DXF를 비교하고 수정본을 확인합니다.").pack(anchor="w", pady=(1, 0))

# 시안 반영: 로고 중심, 따뜻한 오렌지 포인트, 두 줄로 정렬된 검토 정보 카드.
# Tkinter 기본 위젯만 사용하므로 기존 설치환경에서 별도 UI 라이브러리가 필요하지 않다.
CARD_BG = "#FFFCF9"
CARD_ACCENT = "#C76530"
CARD_LINE = "#E9D4C7"
CARD_TEXT = "#27333E"
CARD_BORDER = "#DEAB89"

# 시안과 동일한 균일한 라운드 테두리: 다각형 smooth 곡선 대신
# 고해상도에서 안티앨리어싱 처리한 카드 배경 PNG를 사용한다.
# 원본 회사 로고 / 날짜 / 관리자 기능은 그대로 유지한다.
CARD_WIDTH, CARD_HEIGHT = 432, 184
header_right_outer = tk.Canvas(header, width=CARD_WIDTH, height=CARD_HEIGHT,
                               bg=panel.winfo_toplevel().cget("bg"),
                               bd=0, highlightthickness=0)
header_right_outer.grid(row=0, column=1, sticky="ne", padx=(10, 0))
card_bg_image = tk.PhotoImage(file=str(base / "ui_info_card_bg.png"))
header_right_outer.create_image(0, 0, image=card_bg_image, anchor="nw")
header_right = tk.Frame(header_right_outer, bg=CARD_BG)
header_right_outer.create_window(19, 14, window=header_right, anchor="nw",
                                 width=CARD_WIDTH-42, height=CARD_HEIGHT-37)
# Keep the card at its original 432×184 size; allocate the *interior* 390 px
# explicitly so the calendar and delete controls never spill over the right edge.
header_right.grid_columnconfigure(0, minsize=90)
header_right.grid_columnconfigure(1, weight=1, minsize=280)

# On narrow left panels, move card below title to prevent horizontal clipping.
def arrange_header(event=None):
    if header.winfo_width() < CARD_WIDTH + 260:
        header_right_outer.grid_configure(row=1, column=0, padx=(0, 0), pady=(6, 0), sticky="w")
    else:
        header_right_outer.grid_configure(row=0, column=1, padx=(10, 0), pady=(0, 0), sticky="ne")

header.bind("<Configure>", arrange_header, add="+")


# 회사명은 사용자가 제공한 로고 이미지 그대로 사용하고 가로 비율을 유지한다.
logo_path = base / "daejoo_kores_logo.png"
try:
    company_logo_image = tk.PhotoImage(file=str(logo_path))
    tk.Label(header_right, image=company_logo_image, bg=CARD_BG,
             bd=0, anchor="center").grid(row=0, column=0, columnspan=2,
                                          sticky="ew", pady=(0, 8))
except tk.TclError:
    tk.Label(header_right, text=COMPANY_NAME, font=("Malgun Gothic", 12, "bold"),
             fg=CARD_TEXT, bg=CARD_BG).grid(row=0, column=0, columnspan=2,
                                           sticky="ew", pady=(0, 8))

tk.Frame(header_right, height=1, bg=CARD_LINE).grid(
    row=1, column=0, columnspan=2, sticky="ew", pady=(0, 9)
)

label_font = ("Malgun Gothic", 9, "bold")
review_date = tk.StringVar(value=date.today().isoformat())
tk.Label(header_right, text="검토 날짜", font=label_font, bg=CARD_BG,
         fg=CARD_ACCENT, anchor="w").grid(row=2, column=0, sticky="w", pady=(0, 10))

date_controls = tk.Frame(header_right, bg=CARD_BG, width=280, height=28)
date_controls.grid(row=2, column=1, sticky="ew", pady=(0, 9))
date_controls.grid_propagate(False)
date_controls.grid_columnconfigure(0, weight=1)
date_entry = ttk.Entry(date_controls, textvariable=review_date, width=10,
                       justify="center", style="Compact.TEntry")
date_entry.grid(row=0, column=0, sticky="ew")

# 달력 버튼은 외부 의존성 없이 날짜를 지정할 수 있도록 기본 Tkinter로 구현한다.
def open_review_calendar():
    import calendar
    current = None
    try:
        current = datetime.strptime(review_date.get().strip(), "%Y-%m-%d").date()
    except ValueError:
        current = date.today()
    popup = tk.Toplevel(root)
    popup.title("검토 날짜 선택")
    popup.resizable(False, False)
    popup.transient(root)
    popup.grab_set()
    popup.configure(bg=CARD_BG)
    view_year = tk.IntVar(value=current.year)
    view_month = tk.IntVar(value=current.month)
    title_var = tk.StringVar()
    nav = tk.Frame(popup, bg=CARD_BG, padx=12, pady=10)
    nav.pack(fill="x")
    days_frame = tk.Frame(popup, bg=CARD_BG, padx=12, pady=4)
    days_frame.pack()

    def change_month(delta):
        index = view_year.get() * 12 + view_month.get() - 1 + delta
        index = max(12, min(119999 * 12 - 1, index))
        view_year.set(index // 12)
        view_month.set(index % 12 + 1)
        refresh_days()

    tk.Button(nav, text="◀", command=lambda: change_month(-1), relief="flat",
              bg=CARD_BG, fg=CARD_TEXT, cursor="hand2").pack(side="left")
    tk.Label(nav, textvariable=title_var, font=("Malgun Gothic", 11, "bold"),
             bg=CARD_BG, fg=CARD_TEXT).pack(side="left", expand=True, padx=16)
    tk.Button(nav, text="▶", command=lambda: change_month(1), relief="flat",
              bg=CARD_BG, fg=CARD_TEXT, cursor="hand2").pack(side="right")

    def choose(day):
        review_date.set(f"{view_year.get():04d}-{view_month.get():02d}-{day:02d}")
        popup.destroy()

    def refresh_days():
        for w in days_frame.winfo_children():
            w.destroy()
        title_var.set(f"{view_year.get():04d}년 {view_month.get():02d}월")
        for col, weekday in enumerate(("월", "화", "수", "목", "금", "토", "일")):
            tk.Label(days_frame, text=weekday, width=4, bg=CARD_BG,
                     fg=CARD_ACCENT if col == 6 else CARD_TEXT).grid(row=0, column=col, pady=(0, 3))
        for row_index, week in enumerate(calendar.monthcalendar(view_year.get(), view_month.get()), 1):
            for col, day in enumerate(week):
                if day:
                    tk.Button(days_frame, text=str(day), width=3,
                              command=lambda d=day: choose(d), relief="flat",
                              bg="#FBEFE7" if (day == current.day and view_month.get() == current.month
                                                 and view_year.get() == current.year) else "#FFFFFF",
                              fg=CARD_TEXT, activebackground="#E8C1A9").grid(
                                  row=row_index, column=col, padx=1, pady=1)
    refresh_days()
    popup.update_idletasks()
    x = min(root.winfo_rootx() + root.winfo_width() - popup.winfo_reqwidth() - 12,
            date_entry.winfo_rootx())
    y = date_entry.winfo_rooty() + date_entry.winfo_height() + 5
    popup.geometry(f"+{max(0, x)}+{max(0, y)}")
    popup.focus_force()

calendar_button = ttk.Button(date_controls, text="▦", width=3,
                             command=open_review_calendar, style="CardSmall.TButton")
calendar_button.grid(row=0, column=1, padx=(4, 0))

manager_names = load_managers()
manager_selected = tk.StringVar(value=manager_names[0])
tk.Label(header_right, text="관리자 선택", font=label_font, bg=CARD_BG,
         fg=CARD_ACCENT, anchor="w").grid(row=3, column=0, sticky="w")
manager_controls = tk.Frame(header_right, bg=CARD_BG, width=280, height=28)
manager_controls.grid(row=3, column=1, sticky="ew")
manager_controls.grid_propagate(False)
manager_controls.grid_columnconfigure(0, weight=1)
manager_controls.grid_columnconfigure(1, minsize=78)
manager_controls.grid_columnconfigure(2, minsize=78)

def add_manager():
    name = simpledialog.askstring("관리자 추가", "추가할 관리자 이름을 입력하세요.", parent=root)
    if name is None:
        return
    name = " ".join(name.strip().split())
    if not name:
        messagebox.showwarning("관리자 추가", "관리자 이름을 입력해 주세요.", parent=root)
        return
    if len(name) > 40:
        messagebox.showwarning("관리자 추가", "이름은 40자 이하로 입력해 주세요.", parent=root)
        return
    existing = next((item for item in manager_names if item.casefold() == name.casefold()), None)
    if existing:
        manager_selected.set(existing)
        messagebox.showinfo("관리자 추가", "이미 등록된 관리자입니다.", parent=root)
        return
    updated = [*manager_names, name]
    try:
        persist_managers(updated)
    except OSError as error:
        messagebox.showerror("저장 실패", f"관리자 명단을 저장하지 못했습니다.\n{error}", parent=root)
        return
    manager_names[:] = updated
    manager_dropdown.configure(values=manager_names)
    manager_selected.set(name)

def delete_manager():
    name = manager_selected.get()
    if name not in manager_names:
        return
    if len(manager_names) <= 1:
        messagebox.showwarning("관리자 삭제", "마지막 관리자 1명은 삭제할 수 없습니다.", parent=root)
        return
    if not messagebox.askyesno("관리자 삭제", f"'{name}' 관리자를 목록에서 삭제할까요?\n기존에 저장된 문서 기록은 유지됩니다.", parent=root):
        return
    updated = [item for item in manager_names if item != name]
    try:
        persist_managers(updated)
    except OSError as error:
        messagebox.showerror("관리자 삭제", f"변경 사항을 저장하지 못했습니다.\n{error}", parent=root)
        return
    manager_names[:] = updated
    manager_dropdown.configure(values=manager_names)
    manager_selected.set(manager_names[0])


manager_dropdown = ttk.Combobox(
    manager_controls, textvariable=manager_selected, values=manager_names,
    state="readonly", width=8, font=("Malgun Gothic", 9),
    style="Compact.TCombobox",
)
manager_dropdown.grid(row=0, column=0, sticky="ew")
ttk.Button(manager_controls, text="+ 추가", command=add_manager,
           width=7, style="CardSmall.TButton").grid(row=0, column=1, padx=(4, 0), sticky="ew")
ttk.Button(manager_controls, text="− 삭제", command=delete_manager,
           width=7, style="CardSmall.TButton").grid(row=0, column=2, padx=(4, 0), sticky="ew")


def current_document_key():
    return exercise.get()


def valid_review_date(value):
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None


def document_fields():
    return {
        'company': COMPANY_NAME,
        'date': review_date.get().strip(),
        'manager': manager_selected.get(),
        'drawing_number': drawing_number.get().strip(),
        'part_name': part_name.get().strip(),
        'revision': revision.get().strip(),
        'remarks': remarks.get().strip(),
        'pdf_path': pdf.get(),
        'dxf_path': dxf.get(),
    }


def save_document(show_message=True):
    verified = valid_review_date(review_date.get())
    if not verified:
        messagebox.showwarning('검토 날짜 확인', '검토 날짜는 YYYY-MM-DD 형식의 실제 날짜로 입력해 주세요.', parent=root)
        return False
    review_date.set(verified)
    data = document_fields()
    data['date'] = verified
    updated_documents = {**documents, current_document_key(): data}
    try:
        persist_documents(updated_documents)
    except OSError as error:
        messagebox.showerror('문서 정보 저장 오류', str(error), parent=root)
        return False
    documents.clear()
    documents.update(updated_documents)
    if show_message:
        messagebox.showinfo('문서 정보', f"{current_document_key()}번 문서 정보를 저장했습니다.", parent=root)
    return True


def restore_document():
    info = documents.get(current_document_key(), {})
    review_date.set(info.get('date') or date.today().isoformat())
    manager = info.get('manager')
    if manager in manager_names:
        manager_selected.set(manager)
    drawing_number.set(info.get('drawing_number', ''))
    part_name.set(info.get('part_name', ''))
    revision.set(info.get('revision', ''))
    remarks.set(info.get('remarks', ''))
    document_summary.set(f"도면번호: {drawing_number.get() or '-'}  ·  품명: {part_name.get() or '-'}  ·  Rev: {revision.get() or '-'}")


def open_document_editor():
    popup = tk.Toplevel(root)
    popup.title('문서 정보 입력 및 저장')
    popup.geometry('500x320')
    popup.transient(root)
    popup.grab_set()
    frame = ttk.Frame(popup, padding=16)
    frame.pack(fill='both', expand=True)
    rows = [
        ('도면 번호', drawing_number), ('품명', part_name),
        ('개정(REV)', revision), ('비고', remarks),
    ]
    for index, (caption, variable) in enumerate(rows):
        ttk.Label(frame, text=caption).grid(row=index, column=0, sticky='w', pady=7)
        ttk.Entry(frame, textvariable=variable).grid(row=index, column=1, sticky='ew', padx=(10, 0), pady=7)
    frame.grid_columnconfigure(1, weight=1)
    ttk.Label(frame, text='회사명·검토 날짜·관리자는 메인 화면의 상단 정보와 함께 저장됩니다.',
              foreground='#64748B', wraplength=440).grid(row=4, column=0, columnspan=2, pady=(13, 8), sticky='w')
    actions = ttk.Frame(frame)
    actions.grid(row=5, column=0, columnspan=2, sticky='e')
    def apply():
        if save_document():
            restore_document()
            popup.destroy()
    ttk.Button(actions, text='저장', command=apply).pack(side='left', padx=4)
    ttk.Button(actions, text='취소', command=popup.destroy).pack(side='left', padx=4)


drawing_number = tk.StringVar()
part_name = tk.StringVar()
revision = tk.StringVar()
remarks = tk.StringVar()
document_summary = tk.StringVar()
restore_document()

information_row = ttk.Frame(panel)
information_row.pack(fill='x', pady=(0, 5))
ttk.Label(information_row, textvariable=document_summary, foreground='#4B607A',
          font=('Malgun Gothic', 9)).pack(side='left', fill='x', expand=True)
ttk.Button(information_row, text='문서 정보 입력/저장', command=open_document_editor).pack(side='right')
ttk.Button(information_row, text='정보 저장', command=save_document).pack(side='right', padx=(0, 5))


def card(title):
    frame = ttk.LabelFrame(panel, text=title, padding=(8, 4), style="Step.TLabelframe")
    frame.pack(fill="x", pady=(0, 4))
    return frame


def make_button_grid(parent, items, columns=3):
    """동일 크기 버튼을 균등 배치하고 남는 자리는 비워둔다."""
    for start in range(0, len(items), columns):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0 if start == 0 else 3, 0))
        for col in range(columns):
            row.grid_columnconfigure(col, weight=1, uniform="actions")
        for col, (label, command) in enumerate(items[start:start + columns]):
            ttk.Button(row, text=label, command=command, style="Action.TButton").grid(
                row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0))


inputs = card("01  도면 선택")
selector = ttk.Combobox(inputs, textvariable=exercise, values=tuple(EXERCISES),
                        state="readonly", width=8, style="Compact.TCombobox")
selector.pack(anchor="w", pady=(0, 2))
selector.bind("<<ComboboxSelected>>", select_exercise)
select_exercise()
for label, variable, suffix, color in (
    ("기준 DXF", pdf, "dxf", "#9A4109"),
    ("검토 DXF", dxf, "dxf", "#146648"),
):
    row = ttk.Frame(inputs)
    row.pack(fill="x", pady=1)
    row.grid_columnconfigure(1, weight=1)
    ttk.Label(row, text=label, width=12, foreground=color).grid(row=0, column=0, sticky="w", padx=(0, 6))
    filename = tk.StringVar(value=Path(variable.get()).name)
    variable.trace_add("write", lambda *_, v=variable, f=filename: f.set(Path(v.get()).name))
    ttk.Entry(row, textvariable=filename, state="readonly", style="Compact.TEntry").grid(row=0, column=1, sticky="ew")
    tk.Button(row, text="파일 선택", foreground="white", background=color,
              activeforeground="white", activebackground=color, relief="flat", padx=9, pady=3, font=("Malgun Gothic", 9),
              command=lambda v=variable, ext=suffix: pick(v, ext)).grid(row=0, column=2, sticky="ew", padx=6)
    ttk.Button(row, text="경로 확인", command=lambda v=variable, title=label:
               messagebox.showinfo(title, v.get(), parent=root), style="Action.TButton").grid(row=0, column=3, sticky="ew")

analysis = card("02  분석 및 수정 제안")
# 깊이는 종합 검토에 포함한다. 개별 깊이 검토 버튼은 제공하지 않는다.
status_label = ttk.Label(analysis, textvariable=status, wraplength=540, justify="left")
status_label.pack(anchor="w", fill="x", pady=(2, 0))
analysis.bind("<Configure>", lambda e: status_label.configure(wraplength=max(200, e.width - 24)), add="+")
analysis_actions = ttk.Frame(analysis)
analysis_actions.pack(fill="x", pady=(3, 0))

output = card("03  승인 및 저장")
approve_button = ttk.Button(output, text="수정안 승인 및 DXF 저장",
                            command=lambda: run(save=True), state="disabled", style="Primary.TButton")
approve_button.pack(anchor="w", pady=(0, 2))
for variable in (pdf, dxf):
    variable.trace_add("write", lambda *_: approve_button.configure(state="disabled"))
save_display = tk.StringVar()
folder_button = ttk.Button(output, text="결과 폴더 열기", command=open_result_folder, style="Action.TButton")


def refresh_saved_path(*_):
    path = saved_path.get()
    save_display.set(path or "아직 저장된 수정본이 없습니다.")
    folder_button.configure(state="normal" if path else "disabled")


saved_path.trace_add("write", refresh_saved_path)
refresh_saved_path()
ttk.Entry(output, textvariable=save_display, state="readonly", style="Compact.TEntry").pack(fill="x")
folder_button.pack(anchor="w", pady=(2, 0))

records = card("04  재검토 및 기록")
record_entries = (
    ("다시 검토", lambda: run_integrated_review()),
    ("검토 결과 저장", save_result),
    ("사례 저장", lambda: review_cases(add=True)),
    ("이전 사례 보기", review_cases),
    ("검토용 PDF 저장", save_pdf),
)
make_button_grid(records, record_entries, columns=3)
from drawing_preview import DrawingPreview

# 하단 3열 미리보기: 좌측 작업 패널의 너비 안에서 동일한 크기와 간격 유지.
# 화면 높이에 비례하는 미리보기(기본 약 145~200px).
# 상세 내용은 각 칸 내부에서 스크롤할 수 있다.
PREVIEW_HEIGHT = 215
PREVIEW_GAP = 10
preview_section = ttk.Frame(panel)
preview_section.pack(fill="x", pady=(3, 0))
preview_section.grid_columnconfigure((0, 1, 2), weight=1, uniform="review_previews")
preview_section.grid_rowconfigure(0, minsize=PREVIEW_HEIGHT)


def make_preview_card(column, heading, accent):
    outer = tk.Frame(preview_section, background="#C8D2DD", padx=1, pady=1)
    outer.grid(row=0, column=column, sticky="nsew",
               padx=(0, PREVIEW_GAP if column < 2 else 0))
    outer.grid_propagate(False)
    inner = tk.Frame(outer, background="#FFFFFF")
    inner.pack(fill="both", expand=True)
    tk.Label(inner, text=heading, font=("Segoe UI", 10, "bold"),
             foreground=accent, background="#FFFFFF", anchor="w",
             padx=7, pady=4).pack(fill="x")
    tk.Frame(inner, height=2, background=accent).pack(fill="x")
    return inner


result_card = make_preview_card(0, "검토 결과 미리보기", "#244A80")
case_card = make_preview_card(1, "사례 저장 미리보기", "#17735B")
pdf_card = make_preview_card(2, "검토용 PDF 리스트", "#A65A1F")


def preview_text(parent):
    area = tk.Frame(parent, background="#FFFFFF")
    area.pack(fill="both", expand=True, padx=8, pady=8)
    scrollbar = ttk.Scrollbar(area)
    scrollbar.pack(side="right", fill="y")
    viewer = tk.Text(area, font=("Malgun Gothic", 9), wrap="word", relief="flat",
                     background="#FFFFFF", foreground="#25354B", padx=4, pady=4,
                     yscrollcommand=scrollbar.set, state="disabled", borderwidth=0)
    viewer.pack(side="left", fill="both", expand=True)
    scrollbar.configure(command=viewer.yview)
    return viewer


result_preview_text = preview_text(result_card)
case_preview_text = preview_text(case_card)


def put_preview(widget, content):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


def refresh_result_preview(*_):
    put_preview(result_preview_text, result.get() or
                "검토 결과가 없습니다.\n\n'종합 도면 검토 · 차이 위치 표시' 버튼을 누르면 치수·깊이·형상 비교 결과가 표시됩니다.")


def refresh_case_preview():
    database = base / "review_cases.sqlite3"
    if not database.is_file():
        content = "저장된 판단 사례가 없습니다.\n\n검토 후 '사례 저장' 버튼을 이용하세요."
    else:
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                rows = db.execute("SELECT id, result, reason FROM cases "
                                  "ORDER BY id DESC LIMIT 5").fetchall()
            content = "\n\n".join(
                f"사례 #{number}\n{record}\n판단: {reason}"
                for number, record, reason in rows
            ) or "저장된 판단 사례가 없습니다."
        except sqlite3.Error as error:
            content = f"사례를 불러올 수 없습니다.\n{error}"
    put_preview(case_preview_text, content)


result.trace_add("write", refresh_result_preview)
refresh_result_preview()
refresh_case_preview()

# 검토용 PDF 목록에는 현재 도면에 대응하는 개별 PDF 파일만 표시한다.
# 다른 도면의 미리보기나 현재 DXF의 즉석 렌더링을 재사용하지 않는다.
pdf_list_frame = tk.Frame(pdf_card, background="#FFFFFF")
pdf_list_frame.pack(fill="both", expand=True, padx=8, pady=8)
pdf_list = tk.Listbox(pdf_list_frame, font=("Malgun Gothic", 9), relief="flat",
                      foreground="#25354B", selectbackground="#DCE8F4",
                      selectforeground="#1D3C58", activestyle="none")
pdf_list.pack(side="left", fill="both", expand=True)
pdf_list_scroll = ttk.Scrollbar(pdf_list_frame, orient="vertical", command=pdf_list.yview)
pdf_list_scroll.pack(side="right", fill="y")
pdf_list.configure(yscrollcommand=pdf_list_scroll.set)
pdf_list_paths = []


def refresh_pdf_list(*_):
    pdf_list.delete(0, "end")
    pdf_list_paths.clear()
    source = Path(dxf.get()) if dxf.get() else None
    if source:
        pdf_list.insert("end", "현재 검토 DXF: " + source.name)
        pdf_list.insert("end", "수정본 DXF: " + (Path(corrected_dxf_path.get()).name if corrected_dxf_path.get() else "없음"))
        pdf_list.insert("end", "저장된 PDF: " + (Path(converted_pdf_path.get()).name if converted_pdf_path.get() else "없음"))
    else:
        pdf_list.insert("end", "선택된 DXF가 없습니다.")
        pdf_list.insert("end", "수정본 DXF: 없음")
        pdf_list.insert("end", "저장된 PDF: 없음")
    if source and source.parent.is_dir():
        try:
            # 현재 DXF 파일명과 대응하는 PDF만 목록에 표시.
            matching = source.with_name(source.stem + "_preview.pdf")
            saved = [matching] if matching.is_file() else []
            selected_pdf = Path(converted_pdf_path.get()) if converted_pdf_path.get() else None
            if selected_pdf and selected_pdf.is_file() and selected_pdf not in saved:
                saved.append(selected_pdf)
            for path in saved:
                pdf_list.insert("end", "PDF · " + path.name)
                pdf_list_paths.append(path)
        except OSError:
            pass
    pdf_list.insert("end", "")
    pdf_list.insert("end", "수정본 DXF를 저장하면 오른쪽 하단에 자동 표시됩니다.")


def open_pdf_from_list(event=None):
    selected = pdf_list.curselection()
    if not selected:
        return
    index = selected[0] - 3
    if index < 0 or index >= len(pdf_list_paths):
        return
    try:
        os.startfile(pdf_list_paths[index])
    except OSError as error:
        messagebox.showerror("PDF 열기", str(error), parent=root)

pdf_list.bind("<Double-Button-1>", open_pdf_from_list)


def refresh_active_drawing(*_):
    """A newly selected source never inherits any existing corrected drawing.

    The bottom-right 2D viewer becomes populated only after a successful,
    explicit approval-and-save action in this program session.
    """
    corrected_dxf_path.set("")
    converted_pdf_path.set("")
    saved_path.set("")
    if 'refresh_pdf_list' in globals():
        refresh_pdf_list()


def clear_previews_on_pdf_change(*_):
    # A different reference invalidates the correction for the prior pair.
    corrected_dxf_path.set("")
    converted_pdf_path.set("")
    saved_path.set("")
    if 'refresh_pdf_list' in globals():
        refresh_pdf_list()


dxf.trace_add("write", refresh_active_drawing)
pdf.trace_add("write", clear_previews_on_pdf_change)
refresh_active_drawing()

# 오른쪽 세 개의 2D·3D 행은 스크롤 없이 항상 한 화면에 모두 표시한다.
# 창 크기 변경 시 사용 가능한 세로 공간을 3등분하며 여분 공간을 남기지 않는다.
# 왼쪽 작업 영역 스크롤과 개별 도면의 마우스 확대/축소는 그대로 유지한다.
from shape_viewer import ShapeViewer

right_container = ttk.Frame(root)
right_container.grid(row=0, column=1, sticky="nsew")
right_container.grid_rowconfigure(0, weight=1)
right_container.grid_columnconfigure(0, weight=1)
right_container.grid_propagate(False)

previews = ttk.Frame(right_container, padding=(6, 5, 6, 5))
previews.grid(row=0, column=0, sticky="nsew")
previews.grid_columnconfigure(0, weight=1)
previews.grid_propagate(False)

VIEWER_ROW_GAP = 5
for row_index in range(3):
    previews.grid_rowconfigure(row_index, weight=1, uniform="viewer_height")

viewer_rows = []


def right_preview_container():
    """남는 공간 없이 높이 균등 분배, 각 행의 가로 비율 2D:3D=2:1."""
    index = len(viewer_rows)
    frame = ttk.Frame(previews)
    frame.grid(row=index, column=0, sticky="nsew",
               pady=(0, VIEWER_ROW_GAP if index < 2 else 0))
    # DrawingPreview/ShapeViewer의 초기 요청 크기가 행 비율을 깨뜨리지 않도록 한다.
    frame.grid_propagate(False)
    frame.grid_columnconfigure(0, weight=2, uniform="viewer_pair")
    frame.grid_columnconfigure(1, weight=1, uniform="viewer_pair")
    frame.grid_rowconfigure(0, weight=1)
    drawing_host = ttk.Frame(frame)
    drawing_host.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
    shape_host = ttk.Frame(frame)
    shape_host.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
    viewer_rows.append(frame)
    return drawing_host, shape_host

pdf_preview_host, pdf_shape_host = right_preview_container()
pdf_preview = DrawingPreview(pdf_preview_host, "기준 DXF · 모델 공간", pdf, "#9A4109", is_dxf=True)
pdf_preview.pack(fill="both", expand=True)
base_shape_viewer = ShapeViewer(pdf_shape_host, "원본 3D 형상", "#9A4109")
base_shape_viewer.pack(fill='both', expand=True)

dxf_preview_host, dxf_shape_host = right_preview_container()
dxf_preview = DrawingPreview(dxf_preview_host, "검토 DXF · 모델 공간", dxf,
                             "#146648", is_dxf=True)
dxf_preview.pack(fill="both", expand=True)
review_shape_viewer = ShapeViewer(dxf_shape_host, "검토 3D 형상", "#146648")
review_shape_viewer.pack(fill='both', expand=True)

converted_preview_host, corrected_shape_host = right_preview_container()
compact_pdf_preview = DrawingPreview(
    converted_preview_host, "검토용 PDF·수정 DXF 미리보기", corrected_dxf_path, "#A65A1F",
    is_dxf=True,
    empty_message="저장된 수정본 DXF가 없습니다.\n수정안을 승인하고 DXF로 저장하면 여기에 자동 표시됩니다."
)
compact_pdf_preview.pack(fill="both", expand=True)
# 오른쪽 하단의 기존 수정본 3D 뷰어는 의도적으로 제거한다.
# 이 칸은 앞으로 다른 기능을 넣을 공간으로 확보하며,
# 파일 선택 버튼/3D 렌더러/자동 파일 읽기/상태 문구를 표시하지 않는다.
future_feature_area = tk.Frame(corrected_shape_host, background="#F3F6FA", highlightthickness=0, bd=0)
future_feature_area.pack(fill="both", expand=True)


def compare_locations():
    # 분석이 끝나지 않았더라도 클릭하면 비교 근거를 생성한 뒤 이동한다.
    if not (pdf_preview.box and dxf_preview.box):
        run_integrated_review()
    if pdf_preview.box and dxf_preview.box:
        pdf_preview.focus_mark()
        dxf_preview.focus_mark()
    else:
        messagebox.showinfo("검토 위치 비교", "비교 위치를 찾지 못했습니다. 도면의 검토 결과와 기준 DXF를 확인해 주세요.", parent=root)

before_after = []
showing_before = tk.BooleanVar(value=False)


def toggle_revision():
    showing_before.set(not showing_before.get())
    path, marker, color = before_after[0 if showing_before.get() else 1]
    dxf_preview.refresh(marker, color, source=path)
    dxf_preview.focus_mark()
    toggle_button.configure(text="수정 후 보기" if showing_before.get() else "수정 전 보기")


toggle_button = ttk.Button(output, text="수정 전 보기", command=toggle_revision, state="disabled", style="Action.TButton")
toggle_button.pack(anchor="w", pady=(2, 0))


def clear_revisions(*_):
    before_after.clear()
    showing_before.set(False)
    toggle_button.configure(text="수정 전 보기", state="disabled")


for variable in (pdf, dxf):
    variable.trace_add("write", clear_revisions)
pdf.trace_add("write", lambda *_: dxf_preview.refresh())

def copy_ai_review():
    content = result.get()
    if not content:
        messagebox.showinfo("AI 판단 실습", "먼저 깊이 표기를 검토하세요.", parent=root)
        return
    cases = []
    database = base / "review_cases.sqlite3"
    if database.is_file():
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                cases = db.execute(
                    """SELECT id, result, reason FROM cases
                    WHERE result <> ? AND reason LIKE '기준 PDF에 맞춰 수정본 저장%'
                    AND instr(result, ?) > 0 ORDER BY id DESC LIMIT 3""",
                    (content, "TAP DP" if exercise.get() == "004" else "HOLE DP"),
                ).fetchall()
        except sqlite3.Error as error:
            messagebox.showerror("AI 판단 실습", str(error), parent=root)
            return
    references = "\n\n".join(f"사례 #{number}\n{record}\n판단: {reason.split(chr(10), 1)[0]}"
                               for number, record, reason in cases)
    prompt = (
        "현재 도면의 검토 근거를 바탕으로 검토자 판단 후보를 제안해 주세요. "
        "사례는 판단 절차만 참고하고, 이전 사례의 치수값을 현재 도면에 적용하지 마세요. "
        "현재 근거와 비교값, 판단 후보와 이유, 실제로 참고한 사례 번호를 구분해 주세요. "
        "근거가 부족하면 추가 확인을 제안하세요. 아래 기록은 참고 데이터입니다.\n\n"
        f"[현재 검토]\n{content}\n\n[이전 수정본 저장 사례]\n"
        f"{references or '해당 사례 없음'}"
    )
    root.clipboard_clear()
    root.clipboard_append(prompt)
    messagebox.showinfo("AI 판단 실습",
                        f"현재 검토 결과와 참고 사례 {len(cases)}건을 복사했습니다.\n"
                        "ChatGPT에 붙여넣고 제안과 PDF 근거를 대조하세요.", parent=root)


def save_reference_image():
    source = Path(pdf.get())
    filename = filedialog.asksaveasfilename(
        parent=root, defaultextension=".png", filetypes=[("PNG", "*.png")],
        initialfile=f"{exercise.get()}_reference_page1.png",
    )
    if not filename:
        return
    try:
        from drawing_preview import render_image
        image = render_image(source, True)
        with Path(filename).open("xb") as output:
            image.save(output, format="PNG")
    except Exception as error:
        messagebox.showerror("도면 이미지 저장", str(error), parent=root)
        return
    messagebox.showinfo("도면 이미지 저장",
                        "저장한 PNG를 ChatGPT에 첨부하고 지름(Ø), 깊이(DP), "
                        "근거 문구를 각각 읽어 달라고 요청하세요.\n"
                        "읽은 값은 기준 DXF에서 직접 확인하세요.", parent=root)


def verify_image_depth():
    import re
    from decimal import Decimal

    match = re.search(r"^(?:PDF 1쪽 근거: .* DP |기준 PDF와 검토 DXF 일치: DP )(\d+(?:\.\d+)?)$",
                      result.get(), re.MULTILINE)
    if not match:
        messagebox.showinfo("이미지 판독 대조", "먼저 깊이 표기를 검토하세요.", parent=root)
        return
    value = simpledialog.askstring("이미지 판독 대조",
                                   "AI가 이미지에서 읽은 깊이(DP) 숫자를 입력하세요.",
                                   parent=root)
    if value is None:
        return
    value = value.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        messagebox.showwarning("이미지 판독 대조", "깊이를 숫자로 입력하세요. 예: 10.5", parent=root)
        return
    expected = match[1]
    outcome = ("일치" if Decimal(value) == Decimal(expected)
               else "불일치 · 원본 표기 추가 확인 필요")
    record = f"이미지 판독 대조: AI DP {value} / PDF DP {expected} · {outcome}"
    lines = [line for line in result.get().splitlines()
             if not line.startswith("이미지 판독 대조:")]
    result.set("\n".join([*lines, record]))
    messagebox.showinfo("이미지 판독 대조", record, parent=root)


analysis_row1 = ttk.Frame(analysis_actions)
analysis_row1.pack(fill="x")
analysis_row1.grid_columnconfigure((0, 1), weight=1, uniform="analysis")
compare_button = ttk.Button(analysis_row1, text="DXF·DXF 검토 위치 비교",
                            command=compare_locations, style="Primary.TButton")
compare_button.grid(row=0, column=0, sticky="ew")
ttk.Button(analysis_row1, text="AI 검토 요청 복사", command=copy_ai_review, style="Action.TButton").grid(
    row=0, column=1, sticky="ew", padx=(5, 0)
)

analysis_row2 = ttk.Frame(analysis_actions)
analysis_row2.pack(fill="x", pady=(3, 0))
analysis_row2.grid_columnconfigure((0, 1), weight=1, uniform="analysis")
ttk.Button(analysis_row2, text="기준 DXF 이미지 저장", command=save_reference_image, style="Action.TButton").grid(
    row=0, column=0, sticky="ew"
)
ttk.Button(analysis_row2, text="AI 판독 깊이 대조", command=verify_image_depth, style="Action.TButton").grid(
    row=0, column=1, sticky="ew", padx=(5, 0)
)

# 도면이 실제로 어느 정도 기계 판독 가능한지 먼저 검사한다.
from drawing_inspector import inspect as inspect_drawings, report as inspector_report, save_json as save_inspection_json


def open_drawing_diagnostics():
    paths = (Path(pdf.get()), Path(dxf.get()))
    if not all(path.is_file() for path in paths):
        messagebox.showwarning("도면 판독 환경", "기준 DXF와 검토 DXF를 먼저 선택하세요.", parent=root)
        return
    try:
        from drawing_inspector import dxf_profile
        evidence = {'timestamp': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
                    'pdf': None, 'dxf': dxf_profile(paths[1]),
                    'reference_dxf': dxf_profile(paths[0]), 'warnings': [], 'capabilities': []}
    except Exception as error:
        messagebox.showerror("도면 판독 환경", str(error), parent=root)
        return
    window = tk.Toplevel(root)
    window.title("DXF·DXF 도면 판독 환경 · 분석 근거")
    window.geometry("890x630")
    window.minsize(630, 380)
    top = ttk.Frame(window, padding=10)
    top.pack(fill="x")
    ttk.Label(top, text="도면 데이터 판독 상태", font=("Malgun Gothic", 12, "bold")).pack(side="left")
    def save_diagnostic():
        filename = filedialog.asksaveasfilename(parent=window, defaultextension=".json",
                    initialfile=f"{exercise.get()}_drawing_inspection.json", filetypes=[("JSON", "*.json")])
        if filename:
            try:
                save_inspection_json(evidence, filename)
                messagebox.showinfo("검사 결과 저장", "검사 근거를 JSON으로 저장했습니다.", parent=window)
            except OSError as error:
                messagebox.showerror("저장 실패", str(error), parent=window)
    ttk.Button(top, text="판독 근거 JSON 저장", command=save_diagnostic).pack(side="right")
    textframe = ttk.Frame(window, padding=(10,0,10,10))
    textframe.pack(fill="both", expand=True)
    view = tk.Text(textframe, wrap="word", font=("Malgun Gothic", 10), padx=12, pady=10)
    scroll = ttk.Scrollbar(textframe, command=view.yview)
    scroll.pack(side="right", fill="y")
    view.configure(yscrollcommand=scroll.set)
    view.pack(fill="both", expand=True)
    view.insert("1.0", "[기준 DXF]\n" + str(evidence['reference_dxf']) +
                "\n\n[검토 DXF]\n" + inspector_report(evidence))
    view.configure(state="disabled")


# 투상도 분리와 치수 의미 판독은 검토자 확인을 위한 근거 창으로 별도 제공한다.
# 화면상의 색상 표시는 투상도 *후보* 범위이며 실제 불일치 판정 표시와 구분된다.
from geometry_semantics import semantic_report


def open_view_semantics():
    drawing_paths=(Path(pdf.get()),Path(dxf.get()))
    if not all(p.is_file() for p in drawing_paths):
        messagebox.showwarning("투상도 분석", "기준 DXF와 검토 DXF를 먼저 선택하세요.",parent=root)
        return
    try:
        from geometry_semantics import analyze_views, pair_views
        pdf_data=analyze_views(drawing_paths[0],True)
        dxf_data=analyze_views(drawing_paths[1],True)
        pairs=pair_views(pdf_data,dxf_data)
        report=(f"기준 DXF 투상도 후보 {len(pdf_data['views'])}개 / "
                f"검토 DXF 투상도 후보 {len(dxf_data['views'])}개 / "
                f"대응 후보 {len(pairs)}쌍\n"
                "투상도 분리는 위치 후보이며 단독으로 오류를 확정하지 않습니다.")
    except Exception as exc:
        messagebox.showerror("투상도 분석 실패",str(exc),parent=root)
        return
    win=tk.Toplevel(root)
    win.title("투상도 분리 · 치수 의미 · DXF 형상 객체 검토")
    win.geometry("920x680")
    frame=ttk.Frame(win,padding=10)
    frame.pack(fill="both",expand=True)
    ttk.Label(frame,text="투상도별 형상·치수 판독 (후보)",font=("Malgun Gothic",12,"bold")).pack(anchor="w")
    ttk.Label(frame,text="Ø 지름 · R 반경 · DP 깊이 · M 탭/나사 · 무기호 숫자 길이 후보  |  위치 확인 전 자동 오류 판정 없음",foreground="#45657B").pack(anchor="w",pady=(2,8))
    controls=ttk.Frame(frame)
    controls.pack(fill="x",pady=(0,6))
    def apply_regions():
        pdf_preview.set_review_annotations([{"box":v.box,"color":"#2377B5","shape":"rectangle","label":f"기준 DXF 뷰 {v.index}"} for v in pdf_data['views']])
        dxf_preview.set_review_annotations([{"box":v.box,"color":"#2377B5","shape":"rectangle","label":f"DXF 뷰 {v.index}"} for v in dxf_data['views']])
    ttk.Button(controls,text="투상도 후보를 양쪽 도면에 표시",command=apply_regions).pack(side="left")
    ttk.Button(controls,text="표시 초기화",command=lambda:(pdf_preview.set_review_annotations([]),dxf_preview.set_review_annotations([]))).pack(side="left",padx=6)
    def save_report():
        target=filedialog.asksaveasfilename(parent=win,defaultextension=".txt",initialfile=f"{exercise.get()}_view_geometry_report.txt",filetypes=[("텍스트","*.txt")])
        if target:
            Path(target).write_text(report,encoding="utf-8-sig")
    ttk.Button(controls,text="분석 근거 저장",command=save_report).pack(side="right")
    info=ttk.Frame(frame)
    info.pack(fill="both",expand=True)
    scroll=ttk.Scrollbar(info)
    scroll.pack(side="right",fill="y")
    t=tk.Text(info,wrap="word",font=("Malgun Gothic",10),yscrollcommand=scroll.set,padx=12,pady=12)
    t.pack(side="left",fill="both",expand=True)
    scroll.configure(command=t.yview)
    t.insert("1.0",report)
    t.configure(state="disabled")


# ─── PDF/DXF 형상·치수·공차·선종류·텍스트 비교 (비파괴 별도 검토) ─────────
from difference_review import check_drawing, report_text, CATEGORY
from difference_dialog import DifferenceDialog

extended_area = ttk.Frame(analysis)
extended_area.pack(fill="x", pady=(8, 0))
ttk.Separator(extended_area, orient="horizontal").pack(fill="x", pady=(0, 6))
ttk.Label(extended_area, text="형상·치수·중심선·공차·텍스트 통합 검토",
          font=("Malgun Gothic", 10, "bold"), foreground="#244A80").pack(anchor="w")
ttk.Label(extended_area, text="같은 형상군을 우선 대응시키고 치수·공차를 비교합니다. 불확실한 위치는 표시하지 않습니다.",
          foreground="#526477", font=("Malgun Gothic", 8)).pack(anchor="w", pady=(0, 4))

extended_checks = ttk.Frame(extended_area)
extended_checks.pack(fill="x")
compare_categories = (
    ("dimension", "일반 치수·깊이"), ("diameter", "지름·나사 호칭"),
    ("tolerance", "공차 누락/차이"), ("center", "중심선 누락 후보"),
    ("shape", "형상 관계·외곽선 비교"), ("text", "지시 문구/텍스트"),
)
compare_check_vars = {}
for index, (kind, caption) in enumerate(compare_categories):
    flag = tk.BooleanVar(value=True)
    compare_check_vars[kind] = flag
    ttk.Checkbutton(extended_checks, text=caption, variable=flag).grid(
        row=index // 3, column=index % 3, sticky="w", padx=(0, 12), pady=(0, 2))
for index in range(3):
    extended_checks.grid_columnconfigure(index, weight=1)

last_difference_dialog = None


def reset_difference_highlights(*_):
    pdf_preview.set_review_annotations([])
    dxf_preview.set_review_annotations([])
    pdf_preview.enable_region_mark(None)
    dxf_preview.enable_region_mark(None)
    # A change of drawing invalidates current issue evidence/coordinates.
    global last_difference_dialog
    if last_difference_dialog and last_difference_dialog.winfo_exists():
        last_difference_dialog.destroy()
    last_difference_dialog = None


def run_integrated_review():
    """Native DXF-to-DXF review; guarded depth correction is separately verified."""
    global last_difference_dialog
    selected={kind for kind, variable in compare_check_vars.items() if variable.get()}
    if not selected:
        messagebox.showwarning("검토 항목", "하나 이상의 비교 항목을 선택하세요.", parent=root)
        return
    paths=(Path(pdf.get()),Path(dxf.get()))
    if not all(p.is_file() and p.suffix.lower()=='.dxf' for p in paths):
        messagebox.showwarning("도면 파일", "기준 DXF와 검토 DXF를 각각 선택하세요.", parent=root)
        return
    if paths[0].resolve()==paths[1].resolve():
        messagebox.showwarning("도면 파일", "기준과 검토 DXF는 서로 다른 파일이어야 합니다.", parent=root)
        return
    # Only the unique depth-target recognition can authorize correction.
    depth_note=''
    depth_approved=False
    if 'dimension' in selected:
        depth_ok=run(save=False,report_error=False)
        depth_note=status.get().strip()
        depth_approved=bool(depth_ok) and str(approve_button.cget('state'))=='normal'
    else:
        approve_button.configure(state='disabled')
        status.set('깊이 검토 미선택 · 수정 승인 비활성화')
    try:
        from dxf_pair_review import compare_dxf, report_dxf
        findings=compare_dxf(*paths,selected=selected)
        summary=report_dxf(findings,*paths)
    except Exception as error:
        messagebox.showerror('종합 검토 오류',str(error),parent=root)
        return
    reset_difference_highlights()
    prefix=('[깊이 수정 대상 검증]\n'+depth_note+'\n\n') if depth_note else ''
    result.set(prefix+summary)
    approve_button.configure(state='normal' if depth_approved else 'disabled')
    count_different=sum(f.verdict=='불일치' for f in findings)
    count_review=sum(f.verdict=='확인 필요' for f in findings)
    positioned=sum(bool(f.pdf_box) and bool(f.dxf_box) for f in findings)
    diagnostic_summary.set(f'불일치 {count_different}건 · 확인 필요 {count_review}건 · 양쪽 표시 {positioned}건 / {len(findings)}건')
    last_difference_dialog=DifferenceDialog(root,findings,paths[0],paths[1],pdf_preview,dxf_preview,
                                            reviewer=manager_selected.get())


diagnostic_summary = tk.StringVar(value="검토 전 · 확인된 차이 위치만 도면에 표시합니다.")

ttk.Button(extended_area, text="DXF·DXF 판독 환경 점검 / 근거 확인", command=open_drawing_diagnostics,
           style="Action.TButton").pack(fill="x", pady=(5, 0))
ttk.Button(extended_area, text="투상도 분리 · Ø/길이/형상 객체 분석", command=open_view_semantics,
           style="Action.TButton").pack(fill="x", pady=(3, 0))

extended_run=ttk.Frame(extended_area)
extended_run.pack(fill="x", pady=(6, 0))
ttk.Button(extended_run, text="종합 도면 검토 · 차이 위치 표시",command=run_integrated_review,
           style="Primary.TButton").pack(side="left", fill="x", expand=True)
ttk.Button(extended_run,text="색상 표시 지우기",command=reset_difference_highlights,
           style="Action.TButton").pack(side="left",padx=(5,0))
ttk.Label(extended_area, textvariable=diagnostic_summary, foreground="#465F76",
          font=("Malgun Gothic", 9)).pack(anchor="w", pady=(4, 0))
for variable in (pdf, dxf):
    variable.trace_add('write', reset_difference_highlights)

root.mainloop()

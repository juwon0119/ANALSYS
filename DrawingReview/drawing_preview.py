"""Read-only previews for the currently selected drawing files."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import pymupdf
from PIL import Image, ImageChops, ImageTk

from export_pdf import render_dxf_pdf


def render_image(path, is_dxf, marker=None):
    document = (pymupdf.open(stream=render_dxf_pdf(path, marker), filetype="pdf")
                if is_dxf else pymupdf.open(path))
    with document:
        page = document[0]
        scale = 2200 / max(page.rect.width, page.rect.height)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        if not marker:
            return image
        if is_dxf:
            red, green, _ = image.split()
            box = ImageChops.subtract(red, green).getbbox()
            return Image.merge("RGB", (green, green, green)), box
        hits = page.search_for(marker)
        box = tuple(value * scale for value in hits[0]) if len(hits) == 1 else None
        return image, box


class DrawingPreview(ttk.Frame):
    def __init__(self, parent, title, path, color, *, is_dxf=False, empty_message=None):
        super().__init__(parent)
        self.path = path
        self.is_dxf = is_dxf
        self.empty_message = empty_message or "미리보기 파일이 선택되지 않았습니다."
        self.image = None
        self.zoom = 1
        self.box = None
        self.mark_color = ""
        self.review_annotations = []  # normalized document rectangles, not editable geometry
        self._mark_callback = None
        self._mark_start = None
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text=title, foreground=color).pack(side="left")
        for caption, factor in (("−", 0.8), ("+", 1.25), ("전체", None)):
            ttk.Button(header, text=caption, width=5,
                       command=lambda value=factor: self.set_zoom(value)).pack(side="right")
        ttk.Label(header, text="휠 확대·축소 · 드래그 이동").pack(side="right", padx=6)
        self.focus_button = ttk.Button(self, text="검토 위치 보기",
                                       command=self.focus_mark, state="disabled")
        self.focus_button.pack(anchor="e")
        self.filename = ttk.Label(self, wraplength=380)
        self.filename.pack(anchor="w")
        self.canvas = tk.Canvas(self, width=320, height=125, background="#EDF1F5",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<ButtonPress-3>", self._on_mark_press)
        self.canvas.bind("<B3-Motion>", self._on_mark_motion)
        self.canvas.bind("<ButtonRelease-3>", self._on_mark_release)
        path.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def refresh(self, marker=None, color="", *, source=None):
        self.box = None
        self.mark_color = color
        raw_path = str(source if source is not None else self.path.get()).strip()
        self.image = None
        self.zoom = 1
        if not raw_path:
            self.filename.configure(text="")
            self.error = self.empty_message
            self.focus_button.configure(state="disabled")
            self.draw()
            return
        path = Path(raw_path)
        self.filename.configure(text=path.name)
        try:
            if marker:
                self.image, self.box = render_image(path, self.is_dxf, marker)
            else:
                self.image = render_image(path, self.is_dxf)
            self.error = ""
        except Exception as error:
            self.error = f"미리보기 실패\n{error}"
        self.focus_button.configure(state="normal" if self.box else "disabled")
        self.draw()

    def enable_region_mark(self, callback):
        """Allow one user-confirmed region by right mouse drag (Ctrl-free)."""
        self._mark_callback = callback
        self.canvas.configure(cursor="crosshair" if callback else "")

    def _on_mark_press(self, event):
        if self._mark_callback and self.image is not None:
            self._mark_start=(self.canvas.canvasx(event.x),self.canvas.canvasy(event.y))
            self.canvas.delete("mark_draft")
            return "break"

    def _on_mark_motion(self, event):
        if self._mark_callback and self._mark_start:
            self.canvas.delete("mark_draft")
            x0,y0=self._mark_start
            x1,y1=self.canvas.canvasx(event.x),self.canvas.canvasy(event.y)
            self.canvas.create_rectangle(x0,y0,x1,y1,outline="#248D8A",dash=(4,2),width=2,tags="mark_draft")
            return "break"

    def _on_mark_release(self, event):
        if not self._mark_callback or not self._mark_start:
            return
        self.canvas.delete("mark_draft")
        bounds=self.canvas.bbox("drawing")
        callback=self._mark_callback
        self._mark_callback=None
        self.canvas.configure(cursor="")
        if bounds:
            x0,y0=self._mark_start
            x1,y1=self.canvas.canvasx(event.x),self.canvas.canvasy(event.y)
            left,top,right,bottom=bounds
            if abs(x1-x0)>5 and abs(y1-y0)>5 and right>left and bottom>top:
                bb=(max(0,min(1,(min(x0,x1)-left)/(right-left))),
                    max(0,min(1,(min(y0,y1)-top)/(bottom-top))),
                    max(0,min(1,(max(x0,x1)-left)/(right-left))),
                    max(0,min(1,(max(y0,y1)-top)/(bottom-top))))
                callback(bb)
        self._mark_start=None
        return "break"

    def set_review_annotations(self, annotations):
        """Display evidence boxes/ellipses without modifying the PDF or DXF."""
        self.review_annotations = list(annotations)
        self.draw()

    def focus_review_annotation(self, annotation):
        """Move and zoom to a verified bounding box of the selected finding."""
        if self.image is None or not annotation or not annotation.get("box"):
            return
        self.zoom = 2.6
        self.draw()
        bounds = self.canvas.bbox("drawing")
        if not bounds:
            return
        left, top, right, bottom = bounds
        u0, v0, u1, v1 = annotation["box"]
        x = left + (u0 + u1) / 2 * (right-left)
        y = top + (v0 + v1) / 2 * (bottom-top)
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        scroll_w, scroll_h = max(w, right), max(h, bottom)
        self.canvas.xview_moveto(max(0, (x - w/2) / max(1, scroll_w-w)) if scroll_w > w else 0)
        self.canvas.yview_moveto(max(0, (y - h/2) / max(1, scroll_h-h)) if scroll_h > h else 0)

    def focus_mark(self):
        if not self.box:
            return
        self.zoom = 4
        self.draw()
        left, top, right, bottom = self.canvas.bbox("drawing")
        x0, y0, x1, y1 = self.box
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        x = left + (x0 + x1) / 2 * (right - left) / self.image.width
        y = top + (y0 + y1) / 2 * (bottom - top) / self.image.height
        self.canvas.xview_moveto((x - width / 2) / max(width, right))
        self.canvas.yview_moveto((y - height / 2) / max(height, bottom))

    def on_wheel(self, event):
        if event.delta:
            self.set_zoom(1.25 if event.delta > 0 else 0.8, event.x, event.y)
        return "break"

    def set_zoom(self, factor, x=None, y=None):
        if self.image is None:
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        x = width / 2 if x is None else x
        y = height / 2 if y is None else y
        old_bounds = self.canvas.bbox("drawing")
        if factor is None or old_bounds is None:
            self.zoom = 1
            self.draw()
            return
        left, top, right, bottom = old_bounds
        u = (self.canvas.canvasx(x) - left) / (right - left)
        v = (self.canvas.canvasy(y) - top) / (bottom - top)
        new_zoom = min(6, max(1, self.zoom * factor))
        if new_zoom == self.zoom:
            return
        self.zoom = new_zoom
        self.draw()
        left, top, right, bottom = self.canvas.bbox("drawing")
        self.canvas.xview_moveto((left + u * (right - left) - x) / max(width, right))
        self.canvas.yview_moveto((top + v * (bottom - top) - y) / max(height, bottom))

    def draw(self):
        self.canvas.delete("all")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        if self.image is None:
            self.canvas.create_text(10, 10, text=self.error, anchor="nw",
                                    width=max(1, width - 20))
            return
        scale = min(width / self.image.width, height / self.image.height) * self.zoom
        size = tuple(max(1, round(value * scale)) for value in self.image.size)
        self.photo = ImageTk.PhotoImage(self.image.resize(size, Image.Resampling.LANCZOS))
        self.canvas.create_image(max(0, (width - size[0]) // 2),
                                 max(0, (height - size[1]) // 2),
                                 image=self.photo, anchor="nw", tags="drawing")
        self.canvas.configure(scrollregion=(0, 0, max(width, size[0]), max(height, size[1])))
        if self.box:
            left, top, _, _ = self.canvas.bbox("drawing")
            x0, y0, x1, y1 = self.box
            self.canvas.create_rectangle(left + x0 * scale - 4, top + y0 * scale - 4,
                                         left + x1 * scale + 4, top + y1 * scale + 4,
                                         outline=self.mark_color, width=3)
        bounds = self.canvas.bbox("drawing")
        if not bounds:
            return
        left, top, right, bottom = bounds
        for annotation in self.review_annotations:
            bb = annotation.get("box")
            if not bb or len(bb)!=4:
                continue
            x0, y0, x1, y1 = bb
            x0, x1 = sorted((left + x0*(right-left), left + x1*(right-left)))
            y0, y1 = sorted((top + y0*(bottom-top), top + y1*(bottom-top)))
            pad = 5
            color = annotation.get('color', '#477BE4')
            shape = annotation.get('shape','rectangle')
            fn = self.canvas.create_oval if shape=='oval' else self.canvas.create_rectangle
            fn(x0-pad,y0-pad,x1+pad,y1+pad,outline=color,width=3,tags='review_note')
            label = str(annotation.get('label',''))
            if label:
                self.canvas.create_text(max(12,x0),max(12,y0-9),text=label,
                                        fill=color,font=('Malgun Gothic',9,'bold'),
                                        anchor='sw',tags='review_note')

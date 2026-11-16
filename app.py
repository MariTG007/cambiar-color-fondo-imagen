"""Change the background color of a photo (ideal for a CV / resume photo).

Uses AI (rembg) to separate the person from the background, then fills in the chosen color.
Usage:  python app.py
"""
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageFilter, ImageOps, ImageTk

MODEL_NAME = "u2net_human_seg"  # model specialized in people
PREVIEW_MAX = 520               # maximum size (px) of the previews

PRESETS = {
    "Blanco": "#FFFFFF",
    "Gris claro": "#E6E6E6",
    "Azul claro": "#CFE3F5",
    "Azul institucional": "#2F5D8C",
    "Celeste": "#8EC5E8",
    "Beige": "#EDE3D3",
}


# ------------------------------------------------------------------ logic ---
def hex_to_rgb(color: str) -> tuple:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def compose(photo: Image.Image, alpha: Image.Image, color: str,
            erode: float = 1, soften: float = 1, scale: float = 1.0) -> Image.Image:
    """Paste the person (photo + alpha) onto a solid color background.

    erode  : pixels "eaten" from the edge to remove the halo of the old background.
    soften : edge blur so the cutout looks natural.
    scale  : factor so the radii look the same in the downscaled preview.
    """
    mask = alpha
    e = int(round(erode * scale))
    if e > 0:
        mask = mask.filter(ImageFilter.MinFilter(2 * e + 1))
    s = soften * scale
    if s > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(s))
    result = Image.new("RGB", photo.size, hex_to_rgb(color))
    result.paste(photo, mask=mask)
    return result


def shrink(img: Image.Image, max_side: int) -> Image.Image:
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.LANCZOS)
    return copy


# --------------------------------------------------------------------- GUI ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cambiar fondo de foto")
        self.minsize(980, 620)

        self.session = None        # rembg model (loaded on first use)
        self.photo = None          # original RGB image (full size)
        self.alpha = None          # person mask (full size)
        self.photo_small = None    # downscaled versions for the preview
        self.alpha_small = None
        self.path = None
        self.color = tk.StringVar(value=PRESETS["Azul claro"])
        self.erode = tk.DoubleVar(value=1)
        self.soften = tk.DoubleVar(value=1.5)
        self._tk_imgs = {}

        self._build_ui()

    # ---- interface
    def _build_ui(self):
        side = ttk.Frame(self, padding=12)
        side.pack(side="left", fill="y")

        ttk.Button(side, text="1. Abrir foto…", command=self.open_image).pack(fill="x")
        self.status = ttk.Label(side, text="Abre una foto para empezar.",
                                wraplength=220, foreground="#555")
        self.status.pack(fill="x", pady=(6, 14))

        ttk.Label(side, text="2. Color de fondo").pack(anchor="w")
        grid = ttk.Frame(side)
        grid.pack(fill="x", pady=4)
        for i, (name, hex_color) in enumerate(PRESETS.items()):
            tk.Button(grid, text=name, bg=hex_color, relief="groove", width=14,
                      fg="#000" if sum(hex_to_rgb(hex_color)) > 380 else "#fff",
                      command=lambda h=hex_color: self.set_color(h)
                      ).grid(row=i // 2, column=i % 2, padx=2, pady=2)
        ttk.Button(side, text="Otro color…", command=self.pick_color).pack(fill="x", pady=4)
        self.swatch = tk.Label(side, textvariable=self.color, bg=self.color.get(), height=2)
        self.swatch.pack(fill="x")

        ttk.Label(side, text="Ajuste del borde (quita halo)").pack(anchor="w", pady=(14, 0))
        ttk.Scale(side, from_=0, to=6, variable=self.erode,
                  command=lambda _=None: self.refresh()).pack(fill="x")
        ttk.Label(side, text="Suavizado del borde").pack(anchor="w", pady=(8, 0))
        ttk.Scale(side, from_=0, to=6, variable=self.soften,
                  command=lambda _=None: self.refresh()).pack(fill="x")

        self.save_btn = ttk.Button(side, text="3. Guardar…", command=self.save_image,
                                   state="disabled")
        self.save_btn.pack(fill="x", pady=(20, 0))

        views = ttk.Frame(self, padding=12)
        views.pack(side="left", fill="both", expand=True)
        self.lbl_orig = self._make_view(views, "Original")
        self.lbl_res = self._make_view(views, "Resultado")

    def _make_view(self, parent, title):
        frame = ttk.Frame(parent)
        frame.pack(side="left", fill="both", expand=True, padx=6)
        ttk.Label(frame, text=title, font=("Segoe UI", 10, "bold")).pack()
        lbl = ttk.Label(frame, anchor="center")
        lbl.pack(fill="both", expand=True)
        return lbl

    def _show(self, label, img, key):
        self._tk_imgs[key] = ImageTk.PhotoImage(img)
        label.configure(image=self._tk_imgs[key])

    # ---- actions
    def set_color(self, hex_color):
        self.color.set(hex_color)
        self.swatch.configure(bg=hex_color)
        self.refresh()

    def pick_color(self):
        _, hex_color = colorchooser.askcolor(color=self.color.get(), title="Elige el color de fondo")
        if hex_color:
            self.set_color(hex_color.upper())

    def open_image(self):
        path = filedialog.askopenfilename(
            title="Selecciona la foto",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Todos", "*.*")])
        if not path:
            return
        try:
            img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo abrir la imagen:\n{exc}")
            return
        self.path = Path(path)
        self.photo = img
        self.alpha = None
        self.save_btn.configure(state="disabled")
        self._show(self.lbl_orig, shrink(img, PREVIEW_MAX), "orig")
        self.lbl_res.configure(image="")
        self.status.configure(text="Procesando… (la primera vez descarga el modelo de IA, "
                                   "~170 MB; puede tardar unos minutos)")
        threading.Thread(target=self._remove_background, args=(img,), daemon=True).start()

    def _remove_background(self, img):
        try:
            from rembg import new_session, remove
            if self.session is None:
                self.session = new_session(MODEL_NAME)
            cutout = remove(img, session=self.session, post_process_mask=True)
            alpha = cutout.getchannel("A")
        except Exception as exc:
            self.after(0, lambda: self._on_error(exc))
            return
        self.after(0, lambda: self._on_done(img, alpha))

    def _on_error(self, exc):
        self.status.configure(text="Error al procesar.")
        messagebox.showerror("Error", f"No se pudo quitar el fondo:\n{exc}")

    def _on_done(self, img, alpha):
        if img is not self.photo:   # the user opened another photo in the meantime
            return
        self.alpha = alpha
        self.photo_small = shrink(img, PREVIEW_MAX)
        self.alpha_small = alpha.resize(self.photo_small.size, Image.LANCZOS)
        self.status.configure(text=f"Listo: {self.path.name}")
        self.save_btn.configure(state="normal")
        self.refresh()

    def refresh(self):
        if self.alpha_small is None:
            return
        scale = self.photo_small.width / self.photo.width
        out = compose(self.photo_small, self.alpha_small, self.color.get(),
                      self.erode.get(), self.soften.get(), scale)
        self._show(self.lbl_res, out, "res")

    def save_image(self):
        if self.alpha is None:
            return
        default = f"{self.path.stem}_fondo.jpg"
        path = filedialog.asksaveasfilename(
            title="Guardar foto", initialfile=default, defaultextension=".jpg",
            filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")])
        if not path:
            return
        out = compose(self.photo, self.alpha, self.color.get(),
                      self.erode.get(), self.soften.get())
        if path.lower().endswith(".png"):
            out.save(path)
        else:
            out.save(path, quality=95, subsampling=0)
        messagebox.showinfo("Guardado", f"Imagen guardada en:\n{path}")


if __name__ == "__main__":
    App().mainloop()

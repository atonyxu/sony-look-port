#!/usr/bin/env python3
"""Tkinter front-end for look_port: turn one Sony RAW into 12 Creative Look profiles for its body.

    python look_port_gui.py

Everything runs locally; nothing is uploaded. Generation takes a few seconds (12 tables resampled
from the bundled A7M5 source tables).
"""
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import look_port as LP

# Without this, Windows renders the window at 96 dpi and then scales it, so on a 125/150 % display
# the right-hand widgets are clipped and the text is blurry.
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

COMMON = ["Sony ILCE-7M5", "Sony ILCE-7RM5", "Sony ILCE-7RM4", "Sony ILCE-7RM3",
          "Sony ILCE-7M4", "Sony ILCE-7M3", "Sony ILCE-7CM2", "Sony ILCE-7CR",
          "Sony ILCE-1", "Sony ILCE-9M3", "Sony ILCE-6700", "Sony ILCE-6600"]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sony 创意外观移植工具（ACR & LR）")
        self.geometry("820x880")
        self.minsize(780, 820)
        self.q = queue.Queue()
        self.last_files = []
        self._build()
        self.after(100, self._drain)

    # ------------------------------------------------------------------ layout
    def _build(self):
        pad = dict(padx=10, pady=4)
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        # 1. source raw
        src = ttk.LabelFrame(root, text="1. 素材（可选，用来识别机型）")
        src.pack(fill="x", **pad)
        self.raw_var = tk.StringVar()
        ttk.Entry(src, textvariable=self.raw_var).grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=6)
        ttk.Button(src, text="选择 RAW…", command=self.pick_raw).grid(row=0, column=3, padx=8)
        self.detect_var = tk.StringVar(value="尚未读取")
        ttk.Label(src, textvariable=self.detect_var, foreground="#666").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 6))
        src.columnconfigure(0, weight=1)

        # 2. target
        tgt = ttk.LabelFrame(root, text="2. 目标机型")
        tgt.pack(fill="x", **pad)
        self.model_var = tk.StringVar(value="Sony ILCE-7RM3")
        self.model_var.trace_add("write", lambda *_: self._update_prefix())
        box = ttk.Combobox(tgt, textvariable=self.model_var, values=COMMON)
        box.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=6)
        ttk.Label(tgt, text="可直接输入，例如 Sony ILCE-7RM3A / ILCE-1M2", wraplength=520,
                  foreground="#666").grid(row=1, column=0, columnspan=2, sticky="w", padx=8)

        self.prefix_var = tk.StringVar()
        ttk.Label(tgt, text="文件前缀").grid(row=2, column=0, sticky="e", padx=8, pady=6)
        ttk.Entry(tgt, textvariable=self.prefix_var, width=28).grid(row=2, column=1, sticky="w")

        tgt.columnconfigure(0, weight=1)
        tgt.columnconfigure(1, weight=1)

        # 3. tone emulation (optional, off by default; written into the base dcp)
        tone = ttk.LabelFrame(root, text="3. 影调模仿")
        tone.pack(fill="x", **pad)
        self.tone_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(tone, text="启用影调模仿", variable=self.tone_on,
                        command=self._toggle_tone).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=6)

        ttk.Label(tone, text="DRO 模拟").grid(row=1, column=0, sticky="e", padx=8)
        self.dro_var = tk.StringVar(value="Lv3")
        self.dro_box = ttk.Combobox(tone, textvariable=self.dro_var, state="disabled", width=8,
                                    values=["关", "Lv1", "Lv2", "Lv3", "Lv4", "Lv5"])
        self.dro_box.grid(row=1, column=1, sticky="w")

        ttk.Label(tone, text="曝光微调 EV").grid(row=1, column=2, sticky="e", padx=8)
        self.ev_var = tk.StringVar(value="0")
        self.ev_entry = ttk.Entry(tone, textvariable=self.ev_var, width=7, state="disabled")
        self.ev_entry.grid(row=1, column=3, sticky="w")

        ttk.Label(tone, text="黑位").grid(row=1, column=4, sticky="e", padx=8)
        self.toe_var = tk.StringVar(value="0")
        self.toe_entry = ttk.Entry(tone, textvariable=self.toe_var, width=8, state="disabled")
        self.toe_entry.grid(row=1, column=5, sticky="w")

        ttk.Label(tone, text="默认参数即推荐值（DRO Lv3 / EV 0 / 黑位 0）。",
                  wraplength=700, foreground="#666").grid(row=2, column=0, columnspan=6, sticky="w", padx=8, pady=(0, 6))

        # 4. output
        outp = ttk.LabelFrame(root, text="4. 输出")
        outp.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=str(LP.HERE / "out"))
        ttk.Entry(outp, textvariable=self.out_var).grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=6)
        ttk.Button(outp, text="选择目录…", command=self.pick_out).grid(row=0, column=2, padx=4)
        ttk.Button(outp, text="打开", command=self.open_out).grid(row=0, column=3, padx=8)
        outp.columnconfigure(0, weight=1)

        # 5. actions
        act = ttk.Frame(root)
        act.pack(fill="x", **pad)
        self.gen_btn = ttk.Button(act, text="生成 12 个外观 + 底座", command=self.do_generate)
        self.gen_btn.pack(side="left")
        self.ins_btn = ttk.Button(act, text="生成并安装到 Lightroom", command=lambda: self.do_generate(True))
        self.ins_btn.pack(side="left", padx=8)
        self.clean_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(act, text="清理共享目录同名文件", variable=self.clean_var).pack(side="left", padx=10)
        ttk.Button(act, text="清空日志", command=self.clear).pack(side="right")

        # 5. log
        logf = ttk.LabelFrame(root, text="日志")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=14, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.write("选一张该机型的 RAW（可选），确认机型，然后点“生成”。")
        self.write("生成的 12 个 xmp 必须配合同目录的 *Linear Base (Identity).dcp 使用。")
        self._update_prefix()

    # ------------------------------------------------------------------ actions
    def pick_raw(self):
        p = filedialog.askopenfilename(title="选择该机型的 RAW",
                                       filetypes=[("Sony RAW", "*.ARW *.arw"), ("所有文件", "*.*")])
        if not p:
            return
        self.raw_var.set(p)
        try:
            m = LP.model_from_raw(p)
            self.model_var.set(m)
            self.detect_var.set(f"识别到：{m}")
            self.write(f"读取 {Path(p).name} → {m}")
        except Exception as e:
            self.detect_var.set("识别失败")
            self.write(f"读不出机型：{e}")

    def pick_out(self):
        p = filedialog.askdirectory(title="输出目录")
        if p:
            self.out_var.set(p)

    def open_out(self):
        d = Path(self.out_var.get())
        d.mkdir(parents=True, exist_ok=True)
        import os
        os.startfile(str(d)) if hasattr(os, "startfile") else None

    def clear(self):
        self.log.delete("1.0", "end")

    def _toggle_tone(self):
        state = "normal" if self.tone_on.get() else "disabled"
        self.dro_box.configure(state="readonly" if self.tone_on.get() else "disabled")
        for w in (self.ev_entry, self.toe_entry):
            w.configure(state=state)

    def _update_prefix(self):
        try:
            self.prefix_var.set(LP.short_prefix(LP.normalise_model(self.model_var.get())))
        except Exception:
            self.prefix_var.set("—")

    def do_generate(self, install=False):
        model = self.model_var.get().strip()
        raw = self.raw_var.get().strip() or None
        out = self.out_var.get().strip()
        if not model and not raw:
            messagebox.showwarning("缺参数", "至少要有 RAW 或机型")
            return
        if self.tone_on.get():
            dro = {"关": 0, "Lv1": 1, "Lv2": 2, "Lv3": 3, "Lv4": 4, "Lv5": 5}.get(self.dro_var.get(), 0)
            try:
                ev = float(self.ev_var.get() or 0)
                toe = float(self.toe_var.get() or 0)
            except ValueError:
                messagebox.showwarning("参数错误", "曝光 / 黑位要填数字")
                return
        else:
            dro, ev, toe = 0, 0.0, 0.0
        prefix = self.prefix_var.get().strip()

        self.gen_btn.state(["disabled"])
        self.ins_btn.state(["disabled"])
        self.write("—" * 62)

        def work():
            try:
                r = LP.build(model=model, raw=raw, out_dir=out, log=self.write,
                             ev=ev, toe=toe, dro=dro, prefix=prefix)
                self.last_files = r["files"]
                if install:
                    LP.install(r["files"], clean=self.clean_var.get(), log=self.write)
                self.write(f"完成：{len(r['files'])} 个文件 → {r['out_dir']}")
            except Exception:
                self.write("出错：\n" + traceback.format_exc())
            finally:
                self.q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ plumbing
    def write(self, text):
        self.q.put(("log", str(text)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", payload + "\n")
                    self.log.see("end")
                elif kind == "done":
                    self.gen_btn.state(["!disabled"])
                    self.ins_btn.state(["!disabled"])
        except queue.Empty:
            pass
        self.after(100, self._drain)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()

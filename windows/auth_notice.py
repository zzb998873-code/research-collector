"""Show the login link in the interactive Windows session."""

import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from storage import now, write_json


def show(receipt, url, title):
    window = tk.Tk()
    window.title("科研收集 · 抖音需要登录")
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=20)
    frame.grid()
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text=title, wraplength=440).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frame, text=url, wraplength=440).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 16))

    def open_link():
        webbrowser.open(url)
        window.destroy()

    ttk.Button(frame, text="打开原帖", command=open_link).grid(row=2, column=0, sticky="w")
    ttk.Button(frame, text="稍后", command=window.destroy).grid(row=2, column=1, sticky="e")
    window.attributes("-topmost", True)
    window.after(1000, lambda: window.attributes("-topmost", False))
    window.update_idletasks()
    write_json(receipt, {"url": url, "notified_at": now().isoformat()})
    window.mainloop()


if __name__ == "__main__":
    show(Path(sys.argv[1]), sys.argv[2], sys.argv[3])

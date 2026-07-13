"""Settings window (tkinter). Launched as a SEPARATE process by the tray app
(`--settings`) so it never fights the tray's GUI main thread on macOS.

It reads the current config, lets the user edit it, saves on OK, and can test
the device connection. The tray app reloads config after this process exits.
"""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import config as cfg_mod
from . import autostart


def _test_connection(ip, result_var, btn):
    def worker():
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from smalltv import SmallTV
            ver = SmallTV(ip, timeout=4).get_text("esphome_version")
            # esphome_version is a text_sensor; fall back to alive check
            alive = SmallTV(ip, timeout=4).is_alive()
            result_var.set("● connected" if (ver or alive) else "○ no response")
        except Exception as e:
            result_var.set(f"○ {e}")
        finally:
            btn.config(state="normal")
    btn.config(state="disabled")
    result_var.set("testing…")
    threading.Thread(target=worker, daemon=True).start()


def run():
    cfg = cfg_mod.load()

    root = tk.Tk()
    root.title("SmallTV Widget — Settings")
    root.resizable(False, False)
    pad = {"padx": 10, "pady": 4}

    frm = ttk.Frame(root, padding=14)
    frm.grid(sticky="nsew")

    row = 0
    ttk.Label(frm, text="Device", font=("", 11, "bold")).grid(row=row, column=0, sticky="w", **pad)
    row += 1

    ttk.Label(frm, text="Device IP / host").grid(row=row, column=0, sticky="e", **pad)
    ip_var = tk.StringVar(value=cfg["device_ip"])
    ttk.Entry(frm, textvariable=ip_var, width=22).grid(row=row, column=1, sticky="w", **pad)
    status_var = tk.StringVar(value="")
    test_btn = ttk.Button(frm, text="Test")
    test_btn.grid(row=row, column=2, **pad)
    test_btn.config(command=lambda: _test_connection(ip_var.get().strip(), status_var, test_btn))
    ttk.Label(frm, textvariable=status_var, foreground="#0a0").grid(row=row, column=3, sticky="w", **pad)
    row += 1

    ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
    row += 1

    ttk.Label(frm, text="Stocks bridge", font=("", 11, "bold")).grid(row=row, column=0, sticky="w", **pad)
    row += 1
    ttk.Label(frm, text="Ticker").grid(row=row, column=0, sticky="e", **pad)
    ticker_var = tk.StringVar(value=cfg["stock"]["ticker"])
    ttk.Entry(frm, textvariable=ticker_var, width=12).grid(row=row, column=1, sticky="w", **pad)
    row += 1
    ttk.Label(frm, text="Refresh (seconds)").grid(row=row, column=0, sticky="e", **pad)
    stock_int_var = tk.StringVar(value=str(cfg["stock"]["interval"]))
    ttk.Entry(frm, textvariable=stock_int_var, width=8).grid(row=row, column=1, sticky="w", **pad)
    row += 1
    stock_auto_var = tk.BooleanVar(value=cfg["stock"]["autostart"])
    ttk.Checkbutton(frm, text="Start stocks bridge when the widget launches",
                    variable=stock_auto_var).grid(row=row, column=0, columnspan=4, sticky="w", **pad)
    row += 1

    ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
    row += 1

    ttk.Label(frm, text="PC stats bridge", font=("", 11, "bold")).grid(row=row, column=0, sticky="w", **pad)
    row += 1
    ttk.Label(frm, text="Title").grid(row=row, column=0, sticky="e", **pad)
    pc_title_var = tk.StringVar(value=cfg["pcstats"]["title"])
    ttk.Entry(frm, textvariable=pc_title_var, width=18).grid(row=row, column=1, sticky="w", **pad)
    row += 1
    ttk.Label(frm, text="Refresh (seconds)").grid(row=row, column=0, sticky="e", **pad)
    pc_int_var = tk.StringVar(value=str(cfg["pcstats"]["interval"]))
    ttk.Entry(frm, textvariable=pc_int_var, width=8).grid(row=row, column=1, sticky="w", **pad)
    row += 1
    pc_auto_var = tk.BooleanVar(value=cfg["pcstats"]["autostart"])
    ttk.Checkbutton(frm, text="Start PC stats bridge when the widget launches",
                    variable=pc_auto_var).grid(row=row, column=0, columnspan=4, sticky="w", **pad)
    row += 1

    ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
    row += 1

    login_var = tk.BooleanVar(value=autostart.is_enabled())
    ttk.Checkbutton(frm, text="Start SmallTV Widget at login",
                    variable=login_var).grid(row=row, column=0, columnspan=4, sticky="w", **pad)
    row += 1

    def on_ok():
        try:
            s_int = max(1.0, float(stock_int_var.get()))
            p_int = max(1.0, float(pc_int_var.get()))
        except ValueError:
            messagebox.showerror("Invalid", "Refresh intervals must be numbers.")
            return
        cfg["device_ip"] = ip_var.get().strip() or cfg["device_ip"]
        cfg["stock"]["ticker"] = ticker_var.get().strip().upper()
        cfg["stock"]["interval"] = s_int
        cfg["stock"]["autostart"] = bool(stock_auto_var.get())
        cfg["pcstats"]["title"] = pc_title_var.get().strip() or "PC Monitor"
        cfg["pcstats"]["interval"] = p_int
        cfg["pcstats"]["autostart"] = bool(pc_auto_var.get())
        cfg["start_at_login"] = bool(login_var.get())
        cfg_mod.save(cfg)
        try:
            autostart.set_enabled(cfg["start_at_login"])
        except Exception as e:
            messagebox.showwarning("Start at login", f"Could not update login item:\n{e}")
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=row, column=0, columnspan=4, sticky="e", pady=(10, 0))
    ttk.Button(btns, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=6)
    ttk.Button(btns, text="Save", command=on_ok).grid(row=0, column=1, padx=6)

    root.update_idletasks()
    root.eval("tk::PlaceWindow . center")
    root.mainloop()


if __name__ == "__main__":
    run()

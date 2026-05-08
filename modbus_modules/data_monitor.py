"""
数据监控窗口模块
================
显示底层串口发送(TX)和接收(RX)的原始十六进制数据，
附带时间戳、方向标记、已解析摘要，方便调试与排查。
"""

from __future__ import annotations

import datetime
import tkinter as tk
from tkinter import ttk
from typing import Optional

from modbus_modules.modbus_core import (
    bytes_to_hex_str,
    parse_modbus_response,
)


class DataMonitor:
    """
    数据监控窗口

    用法:
        monitor = DataMonitor(root, serial_mgr)
        monitor.show()  # 显示窗口
    """

    def __init__(self, parent: tk.Tk, serial_mgr):
        self._parent = parent
        self._serial_mgr = serial_mgr
        self._window: Optional[tk.Toplevel] = None
        self._paused = False
        self._auto_scroll = True
        self._entry_count = 0
        self._max_entries = 5000

        # 颜色
        self._color_tx = "#1A6DAB"
        self._color_rx = "#2E7D32"
        self._color_info = "#555555"
        self._color_bg = "#1E1E1E"
        self._color_text = "#D4D4D4"

        self._saved_callbacks = {}

    # ----------------------------------------------------------------
    def show(self):
        if self._window is not None and self._window.winfo_exists():
            self._window.lift()
            self._window.focus()
            return
        self._window = tk.Toplevel(self._parent)
        self._window.title("🔍 Modbus 数据监控")
        self._window.geometry("900x550")
        self._window.minsize(600, 300)
        self._window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._hook_callbacks()

    def _on_close(self):
        self._unhook_callbacks()
        if self._window and self._window.winfo_exists():
            self._window.destroy()
        self._window = None

    # ----------------------------------------------------------------
    def _build_ui(self):
        win = self._window
        toolbar = ttk.Frame(win, padding=3)
        toolbar.pack(fill=tk.X)
        ttk.Label(
            toolbar, text="📡 串口实时数据监控", font=("微软雅黑", 10, "bold")
        ).pack(side=tk.LEFT)
        self._pause_btn = ttk.Button(
            toolbar, text="⏸ 暂停", command=self._toggle_pause, width=8
        )
        self._pause_btn.pack(side=tk.RIGHT, padx=2)
        self._scroll_btn = ttk.Button(
            toolbar, text="📌 自动滚屏 ON", command=self._toggle_autoscroll, width=14
        )
        self._scroll_btn.pack(side=tk.RIGHT, padx=2)
        ttk.Button(toolbar, text="🗑 清空", command=self._clear_log, width=6).pack(
            side=tk.RIGHT, padx=2
        )

        ttk.Separator(win, orient=tk.HORIZONTAL).pack(fill=tk.X)

        stats = ttk.Frame(win, padding=2)
        stats.pack(fill=tk.X)
        self._stats_tx = ttk.Label(stats, text="TX: 0", foreground=self._color_tx)
        self._stats_tx.pack(side=tk.LEFT, padx=5)
        self._stats_rx = ttk.Label(stats, text="RX: 0", foreground=self._color_rx)
        self._stats_rx.pack(side=tk.LEFT, padx=15)
        self._stats_err = ttk.Label(stats, text="异常: 0", foreground="#C62828")
        self._stats_err.pack(side=tk.LEFT, padx=5)

        ttk.Label(stats, text="搜索:").pack(side=tk.RIGHT, padx=(10, 2))
        self._search_var = tk.StringVar()
        self._search_var.trace("w", lambda *a: self._apply_filter())
        ttk.Entry(stats, textvariable=self._search_var, width=20).pack(side=tk.RIGHT)

        import tkinter.scrolledtext as st

        main_f = ttk.Frame(win)
        main_f.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._text = st.ScrolledText(
            main_f,
            wrap=tk.NONE,
            font=("Consolas", 10),
            bg=self._color_bg,
            fg=self._color_text,
            insertbackground=self._color_text,
            state=tk.DISABLED,
            padx=6,
            pady=4,
            relief=tk.FLAT,
            borderwidth=0,
        )
        self._text.pack(fill=tk.BOTH, expand=True)
        self._text.tag_configure("tx", foreground=self._color_tx)
        self._text.tag_configure("rx", foreground=self._color_rx)
        self._text.tag_configure("info", foreground=self._color_info)
        self._text.tag_configure("error", foreground="#FF5252")
        self._text.tag_configure(
            "dir_tx", foreground=self._color_tx, font=("Consolas", 10, "bold")
        )
        self._text.tag_configure(
            "dir_rx", foreground=self._color_rx, font=("Consolas", 10, "bold")
        )
        self._text.tag_configure("sep", foreground="#333333")
        self._text.bind("<Control-a>", lambda e: self._select_all())
        self._text.bind("<Control-c>", lambda e: self._copy_selection())
        self._text.bind("<MouseWheel>", self._on_mousewheel)

        self._status_var = tk.StringVar(value="监控已开启，等待数据...")
        ttk.Label(
            win, textvariable=self._status_var, foreground="gray", font=("Consolas", 8)
        ).pack(fill=tk.X, padx=5, pady=(0, 2), side=tk.BOTTOM)

    # ----------------------------------------------------------------
    def _hook_callbacks(self):
        mgr = self._serial_mgr
        self._saved_callbacks = {
            "on_send": mgr.on_send,
            "on_received": mgr.on_received,
        }
        mgr.on_send = self._on_data_sent
        mgr.on_received = self._on_data_received

    def _unhook_callbacks(self):
        mgr = self._serial_mgr
        for key, cb in self._saved_callbacks.items():
            setattr(mgr, key, cb)
        self._saved_callbacks.clear()

    # ----------------------------------------------------------------
    def _on_data_sent(self, data: bytes):
        if self._paused:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:12]
        hex_str = bytes_to_hex_str(data)
        parsed = self._try_parse_frame(data)
        summary = self._format_parsed_summary(parsed)
        self._parent.after(
            0,
            lambda: self._append_entry(
                ts, "TX→", hex_str, summary, "tx", self._color_tx
            ),
        )

    def _on_data_received(self, frame: bytes, parsed: dict):
        if not self._paused:
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:12]
            hex_str = bytes_to_hex_str(frame)
            summary = self._format_parsed_summary(parsed)
            is_err = "error" in parsed
            tag = "error" if is_err else "rx"
            color = self._color_rx if not is_err else "#FF5252"
            self._parent.after(
                0, lambda: self._append_entry(ts, "RX←", hex_str, summary, tag, color)
            )
        if self._saved_callbacks.get("on_received"):
            self._saved_callbacks["on_received"](frame, parsed)

    def _try_parse_frame(self, data: bytes) -> dict:
        if len(data) < 4:
            return {"info": "数据过短"}
        result = parse_modbus_response(data)
        if "error" not in result:
            return result
        slave = data[0] if len(data) > 0 else 0
        func = data[1] if len(data) > 1 else 0
        from modbus_modules.modbus_core import MODBUS_FUNCTIONS

        return {
            "slave_id": slave,
            "function": func,
            "function_name": MODBUS_FUNCTIONS.get(func, f"未知(0x{func:02X})"),
        }

    def _format_parsed_summary(self, parsed: dict) -> str:
        if "error" in parsed and "exception_code" not in parsed:
            return f"⚠ {parsed['error']}"
        parts = []
        if "slave_id" in parsed:
            parts.append(f"从站{parsed['slave_id']}")
        if "function" in parsed:
            fname = parsed.get("function_name", "")
            if len(fname) > 14:
                fname = fname[:14] + "…"
            parts.append(f"FC=0x{parsed['function']:02X} {fname}")
        if "registers" in parsed:
            rs = parsed["registers"]
            if len(rs) <= 4:
                parts.append(f"reg=[{','.join(str(r) for r in rs)}]")
            else:
                parts.append(f"reg=[{rs[0]},{rs[1]}…共{len(rs)}个]")
        if "exception_code" in parsed:
            parts.append(f"异常码=0x{parsed['exception_code']:02X}")
        return " | ".join(parts) if parts else ""

    # ----------------------------------------------------------------
    def _append_entry(self, ts, direction, hex_str, summary, tag, color):
        self._entry_count += 1
        t = self._text
        t.config(state=tk.NORMAL)
        dir_tag = "dir_tx" if "TX" in direction else "dir_rx"
        t.insert(tk.END, f"[{ts}] [{direction}]  {hex_str}\n", (tag, dir_tag))
        if summary:
            t.insert(tk.END, f"           └─ {summary}\n", ("info",))
        t.insert(tk.END, "─" * 50 + "\n", ("sep",))
        if self._auto_scroll:
            t.see(tk.END)
        if "TX" in direction:
            self._update_stat("tx")
        elif "异常" in summary or "error" in summary.lower():
            self._update_stat("err")
        else:
            self._update_stat("rx")
        t.config(state=tk.DISABLED)
        if self._entry_count > self._max_entries + 100:
            self._truncate_log()

    def _truncate_log(self):
        t = self._text
        t.config(state=tk.NORMAL)
        end = t.index(f"@-{self._max_entries // 3} lines")
        t.delete("1.0", end)
        t.config(state=tk.DISABLED)
        self._entry_count = len(t.get("1.0", tk.END).split("\n"))

    # ----------------------------------------------------------------
    def _toggle_pause(self):
        self._paused = not self._paused
        self._pause_btn.config(text="▶ 继续" if self._paused else "⏸ 暂停")

    def _toggle_autoscroll(self):
        self._auto_scroll = not self._auto_scroll
        self._scroll_btn.config(
            text=f"📌 自动滚屏 {'ON' if self._auto_scroll else 'OFF'}"
        )

    def _clear_log(self):
        t = self._text
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.config(state=tk.DISABLED)
        self._entry_count = 0
        self._stats_tx.config(text="TX: 0")
        self._stats_rx.config(text="RX: 0")
        self._stats_err.config(text="异常: 0")

    def _select_all(self):
        self._text.tag_add(tk.SEL, "1.0", tk.END)
        return "break"

    def _copy_selection(self):
        try:
            sel = self._text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self._window.clipboard_clear()
            self._window.clipboard_append(sel)
        except tk.TclError:
            pass

    def _on_mousewheel(self, event):
        if event.delta > 0 and self._auto_scroll:
            self._auto_scroll = False
            self._scroll_btn.config(text="📌 自动滚屏 OFF")

    def _apply_filter(self):
        kw = self._search_var.get().strip().lower()
        t = self._text
        t.tag_remove("highlight", "1.0", tk.END)
        if not kw:
            return
        t.tag_configure("highlight", background="#FFD54F", foreground="#000000")
        start = "1.0"
        while True:
            pos = t.search(kw, start, tk.END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(kw)}c"
            t.tag_add("highlight", pos, end)
            start = end

    def _update_stat(self, st):
        lbl = {"tx": self._stats_tx, "rx": self._stats_rx, "err": self._stats_err}[st]
        cur = int(lbl.cget("text").split(":")[1].strip())
        lbl.config(
            text=f"{'TX' if st == 'tx' else 'RX' if st == 'rx' else '异常'}: {cur + 1}"
        )

"""
数据表格模块
============
显示协议配置格式 + 实时数据，支持颜色标记和自动更新。
选中写组 (FC=0x10) 时，"转换值"列支持双击编辑直接写入。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from modbus_modules.config_parser import AppConfig, GroupConfig
from modbus_modules.polling_engine import GroupPollResult, PointValue


class ToolTip:
    """悬浮提示：鼠标悬停在控件上时显示完整文本"""

    def __init__(self, widget):
        self._widget = widget
        self._tip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None
        self._text = ""

    def show_tip(self, text: str, x: int, y: int):
        """在指定位置显示提示"""
        self._text = text
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None
        if not text:
            return
        self._tip_window = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x + 15}+{y + 10}")
        label = tk.Label(
            tw,
            text=text,
            justify=tk.LEFT,
            background="#FFFFDD",
            foreground="#333333",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Consolas", 9),
            padx=6,
            pady=3,
            wraplength=500,
        )
        label.pack()

    def hide_tip(self):
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None
        self._text = ""

    def schedule(self, text: str, x: int, y: int, delay_ms: int = 400):
        """延时显示提示，避免频繁闪烁"""
        self.cancel()
        self._after_id = self._widget.after(delay_ms, lambda: self.show_tip(text, x, y))

    def cancel(self):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None


QUALITY_COLORS = {
    "good": "",
    "error": "#FFCCCC",
    "timeout": "#FFE0CC",
    "alarm_low": "#CCE5FF",
    "alarm_high": "#FFCCCC",
}

# "转换值"位列索引 (values tuple 中的位置)
# name(0) addr(1) dtype(2) scale(3) offset(4) unit(5) desc(6) raw(7) value(8) quality(9)
COL_VALUE = 8
COL_RAW = 7


class DataTable(ttk.Frame):
    """数据显示表格，支持内联编辑"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._data_cache: dict[str, PointValue] = {}
        self.edit_mode = False
        self._current_group: Optional[GroupConfig] = None
        self._edit_entry: Optional[tk.Entry] = None
        self._edit_item: Optional[str] = None
        self._column_config: list[
            tuple[str, str, int, str]
        ] = []  # (id, title, width, anchor)
        self._column_visible: dict[str, bool] = {}
        self._setup_treeview()
        self._setup_context_menu()
        self._setup_edit_bindings()
        self._setup_tooltip()

    def _setup_treeview(self):
        columns = (
            "name",
            "address",
            "data_type",
            "scale",
            "offset",
            "unit",
            "description",
            "raw_value",
            "value",
            "quality",
        )
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=18,
        )
        self._column_config = [
            ("name", "点位名称", 140, "w"),
            ("address", "地址", 80, "center"),
            ("data_type", "数据类型", 78, "center"),
            ("scale", "倍率", 58, "center"),
            ("offset", "偏移", 58, "center"),
            ("unit", "单位", 48, "center"),
            ("description", "说明", 120, "w"),
            ("raw_value", "原始值", 120, "center"),
            ("value", "转换值", 130, "e"),
            ("quality", "状态", 68, "center"),
        ]
        for col_id, title, width, anchor in self._column_config:
            self.tree.heading(col_id, text=title)
            self.tree.column(col_id, width=width, anchor=anchor, minwidth=40)  # type: ignore[arg-type]
            self._column_visible[col_id] = True

        vbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Treeview", rowheight=24, font=("Consolas", 9))
        style.configure("Treeview.Heading", font=("微软雅黑", 9, "bold"))

    def _setup_context_menu(self):
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="复制选中", command=self._copy_selected)
        self.menu.add_command(label="复制全部", command=self._copy_all)
        # 列标题右键菜单：选择显示/隐藏列
        self.col_menu = tk.Menu(self, tearoff=0)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Control-c>", lambda e: self._copy_selected())

    def _on_tree_right_click(self, event):
        """右键点击：判断点击区域，显示对应菜单"""
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            # 标题区域右键：显示列选择菜单
            self._show_column_menu(event)
        else:
            # 数据区域右键：显示复制菜单
            self.menu.post(event.x_root, event.y_root)

    def _show_column_menu(self, event):
        """显示列选择右键菜单"""
        self.col_menu.delete(0, "end")
        for col_id, title, width, anchor in self._column_config:
            visible = self._column_visible.get(col_id, True)
            prefix = "✓ " if visible else "  "
            self.col_menu.add_command(
                label=f"{prefix}{title}",
                command=lambda c=col_id, w=width: self._toggle_column(c, w),
            )
        self.col_menu.post(event.x_root, event.y_root)

    def _toggle_column(self, col_id: str, default_width: int):
        """切换列的显示/隐藏"""
        self._column_visible[col_id] = not self._column_visible.get(col_id, True)
        # 重新构建显示列列表
        visible_cols = [
            cid
            for cid, _, _, _ in self._column_config
            if self._column_visible.get(cid, True)
        ]
        if len(visible_cols) == len(self._column_config):
            self.tree.configure(displaycolumns=("#all",))
        else:
            self.tree.configure(displaycolumns=visible_cols)

    def _setup_tooltip(self):
        """悬浮提示：鼠标悬停时显示单元格完整文本"""
        self._tooltip = ToolTip(self.tree)
        self.tree.bind("<Motion>", self._on_mouse_move)
        self.tree.bind("<Leave>", lambda e: self._tooltip.hide_tip())

    def _on_mouse_move(self, event):
        """鼠标移动时检测悬停的单元格，显示完整文本"""
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item or not col:
            self._tooltip.hide_tip()
            return
        col_index = int(col.replace("#", "")) - 1
        values = self.tree.item(item, "values")
        if col_index < len(values):
            text = values[col_index]
            # 文本超过8个字符时显示悬浮提示
            if text and len(text) > 8:
                self._tooltip.schedule(text, event.x_root, event.y_root)
            else:
                self._tooltip.hide_tip()
        else:
            self._tooltip.hide_tip()

    def _setup_edit_bindings(self):
        self.tree.bind("<Double-1>", self._on_double_click)

    def _copy_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        lines = ["\t".join(self.tree.item(i, "values")) for i in sel]
        self.winfo_toplevel().clipboard_clear()
        self.winfo_toplevel().clipboard_append("\n".join(lines))

    def _copy_all(self):
        cols = [self.tree.heading(c)["text"] for c in self.tree["columns"]]
        lines = ["\t".join(cols)]
        for item in self.tree.get_children():
            lines.append("\t".join(self.tree.item(item, "values")))
        self.winfo_toplevel().clipboard_clear()
        self.winfo_toplevel().clipboard_append("\n".join(lines))

    # ================================================================
    # 内联编辑
    # ================================================================
    def _on_double_click(self, event):
        if not self.edit_mode:
            return
        col = self.tree.identify_column(event.x)
        if col != f"#{COL_VALUE + 1}":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self._edit_cell(item)

    def _edit_cell(self, item: str):
        self._cancel_edit()
        self._edit_item = item
        cur_val = self.tree.item(item, "values")
        if not cur_val or len(cur_val) <= COL_VALUE:
            return
        old_text = cur_val[COL_VALUE]
        if old_text in ("---", "等待", ""):
            old_text = "0"
        bbox = self.tree.bbox(item, f"#{COL_VALUE + 1}")
        if not bbox:
            return
        self._edit_entry = tk.Entry(
            self.tree,
            font=("Consolas", 9),
            justify=tk.RIGHT,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self._edit_entry.place(
            x=bbox[0],
            y=bbox[1],
            width=max(bbox[2], 40),
            height=bbox[3],
        )
        self._edit_entry.insert(0, old_text)
        self._edit_entry.select_range(0, tk.END)
        self._edit_entry.icursor(tk.END)
        self._edit_entry.focus_set()
        self._edit_entry.bind("<Return>", self._commit_edit)
        self._edit_entry.bind("<Escape>", lambda e: self._cancel_edit())
        self._edit_entry.bind("<FocusOut>", self._commit_edit)

    def _commit_edit(self, event=None):
        if not self._edit_entry or not self._edit_item:
            return
        new_val = self._edit_entry.get().strip()
        item_id = self._edit_item
        cur = list(self.tree.item(item_id, "values"))
        if len(cur) > COL_VALUE:
            cur[COL_VALUE] = new_val
            # 自动反算原始协议值
            try:
                scale = float(cur[3]) if cur[3] not in ("", "1") else 1.0
                offset = float(cur[4]) if cur[4] not in ("", "0") else 0.0
                display = float(new_val)
                raw = int((display - offset) / scale)
                if raw < 0:
                    raw += 0x10000
                raw = raw & 0xFFFF
                cur[COL_RAW] = f"0x{raw:04X}"
            except (ValueError, ZeroDivisionError):
                pass
            self.tree.item(item_id, values=tuple(cur))
            self.tree.tag_configure("pending_write", background="#FFF9C4")
            self.tree.item(item_id, tags=("pending_write",))
        self._cancel_edit()

    def _cancel_edit(self, event=None):
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None
        self._edit_item = None

    # ================================================================
    # 获取写组值
    # ================================================================
    def get_write_values(self) -> dict[str, str]:
        """获取用户已编辑的写入值（忽略提示文字）"""
        result = {}
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if len(vals) > COL_VALUE:
                name = vals[0]  # name 列
                val = vals[COL_VALUE].strip()
                if val and val not in ("---", "等待", "双击编辑", ""):
                    result[name] = val
        return result

    def clear_write_tags(self):
        for item in self.tree.get_children():
            if self.tree.item(item, "tags") == ("pending_write",):
                self.tree.item(item, tags=())

    # ================================================================
    # 加载配置
    # ================================================================
    def load_from_config(self, config: AppConfig):
        self.clear_all()
        for group in config.groups:
            if not group.enabled:
                continue
            self._insert_group_points(group)

    def show_group(self, config: AppConfig, group_name: str):
        self.clear_all()
        self._current_group = None
        self.edit_mode = False
        for group in config.groups:
            if group.name == group_name and group.enabled:
                self._current_group = group
                if group.function_code == 0x10:
                    self.edit_mode = True
                self._insert_group_points(group)
                break

    def _insert_group_points(self, group):
        for point in group.points:
            idx = point.register_index
            dtype = point.data_type
            scale_str = f"{point.scale:g}" if point.scale != 1.0 else "1"
            offset_str = f"{point.offset:g}" if point.offset != 0 else "0"
            hint = "双击编辑" if group.function_code == 0x10 else "---"
            desc = point.description if point.description else ""
            self.tree.insert(
                "",
                tk.END,
                iid=f"point_{point.name}_{idx}",
                values=(
                    point.name,
                    str(group.start_address + idx),
                    dtype,
                    scale_str,
                    offset_str,
                    point.unit,
                    desc,
                    "---",
                    hint,
                    "等待",
                ),
            )

    # ================================================================
    # 更新实时数据
    # ================================================================
    def update_from_result(self, result: GroupPollResult):
        for point in result.points:
            self._data_cache[point.name] = point
            self._update_row(point)

    def _update_row(self, point: PointValue):
        item_id = f"point_{point.name}_{point.register_index}"
        if not self.tree.exists(item_id):
            return
        if isinstance(point.converted_value, float):
            display_val = f"{point.converted_value:.1f}"
        elif point.converted_value is None:
            display_val = "---"
        else:
            display_val = str(point.converted_value)
        quality_map = {
            "good": "正常",
            "error": "错误",
            "timeout": "超时",
            "alarm_low": "低限",
            "alarm_high": "高限",
        }
        quality_text = quality_map.get(point.quality, point.quality)
        if isinstance(point.raw_value, int):
            raw = f"0x{point.raw_value:04X}"
        elif isinstance(point.raw_value, list):
            raw = " ".join(f"0x{r:04X}" for r in point.raw_value)
        else:
            raw = point.hex_str or "---"

        cur = list(self.tree.item(item_id, "values"))
        if len(cur) >= 10:
            cur[COL_RAW] = raw
            if not self.edit_mode:
                cur[COL_VALUE] = display_val
            cur[9] = quality_text
            self.tree.item(item_id, values=tuple(cur))

        bg = QUALITY_COLORS.get(point.quality, "")
        if bg:
            self.tree.tag_configure(f"q_{point.quality}", background=bg)
            self.tree.item(item_id, tags=(f"q_{point.quality}",))
        elif self.tree.item(item_id, "tags") == (f"q_{point.quality}",):
            self.tree.item(item_id, tags=())

    # ================================================================
    # 工具
    # ================================================================
    def clear_all(self):
        self._cancel_edit()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._data_cache.clear()
        self._current_group = None
        self.edit_mode = False

    def get_point_value(self, name: str) -> Optional[PointValue]:
        return self._data_cache.get(name)

    def get_all_values(self) -> dict[str, PointValue]:
        return dict(self._data_cache)

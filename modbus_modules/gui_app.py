"""
主 GUI 应用模块
===============
简洁、单页面的 Modbus 监控上位机界面。
"""

from __future__ import annotations

import datetime
import os
import threading
import time
import tkinter as tk
import tkinter.scrolledtext as scrolledtext
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from modbus_modules.config_parser import (
    AppConfig,
    generate_example_config,
    load_config,
    parse_config_dict,
    validate_config,
)
from modbus_modules.data_monitor import DataMonitor
from modbus_modules.data_table import DataTable
from modbus_modules.group_navigator import GroupNavigator
from modbus_modules.modbus_core import (
    MODBUS_FUNCTIONS,
    add_crc,
    build_modbus_frame,
    bytes_to_hex_str,
    hex_str_to_bytes,
    parse_modbus_response,
)
from modbus_modules.polling_engine import GroupPollResult, PollingEngine
from modbus_modules.serial_manager import SerialManager


class ModbusMasterApp:
    """Modbus 主机上位机主应用"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Modbus 上位机 v2.0")
        self.root.geometry("1300x780")
        self.root.minsize(1000, 620)

        # 核心组件
        self.serial_mgr = SerialManager()
        self.polling_engine = PollingEngine()
        self.app_config: Optional[AppConfig] = None
        self.config_filepath: str = ""

        # 数据监控窗口
        self.data_monitor = DataMonitor(self.root, self.serial_mgr)

        # 回调绑定
        self.serial_mgr.on_received = self._on_serial_received
        self.serial_mgr.on_error = self._on_serial_error
        self.serial_mgr.on_status_change = self._on_serial_status_change
        self.polling_engine.on_poll_result = self._on_poll_result
        self.polling_engine.on_status_change = self._on_polling_status_change
        self.polling_engine.send_request = self._send_poll_request

        # 同步锁
        self._sync_response: Optional[bytes] = None
        self._sync_event = threading.Event()
        self._sync_lock = threading.Lock()

        # 写组状态
        self._current_write_group: Optional[str] = None

        self._build_ui()
        self.refresh_ports()
        self._update_ui_state()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        """构建整体界面（单页）"""
        self._build_menu()

        main = ttk.Frame(self.root, padding=5)
        main.pack(fill=tk.BOTH, expand=True)

        # 第1行：串口
        self._build_serial_row(main)
        # 第2行：配置 + 轮循
        self._build_config_row(main)
        # 主体：左侧组导航 + 右侧数据表
        content = ttk.Frame(main)
        content.pack(fill=tk.BOTH, expand=True, pady=(3, 3))

        # 左侧组导航
        nav_frame = ttk.Frame(content, width=240)
        nav_frame.pack(side=tk.LEFT, fill=tk.Y)
        nav_frame.pack_propagate(False)
        self.group_nav = GroupNavigator(nav_frame)
        self.group_nav.pack(fill=tk.BOTH, expand=True)
        self.group_nav.on_group_selected = self._on_group_selected

        # 分隔线
        ttk.Separator(content, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=3)

        # 右侧数据表格
        self.data_table = DataTable(content)
        self.data_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 底部：Hex 命令 + 状态
        self._build_bottom_row(main)
        # 状态栏
        self._build_status_bar()

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        fm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=fm)
        fm.add_command(label="加载配置", command=self.load_config_dialog)
        fm.add_command(label="导出CSV", command=self.export_data)
        fm.add_command(label="🔍 数据监控", command=self.open_data_monitor)
        fm.add_separator()
        fm.add_command(label="退出", command=self.on_closing)

    def _build_serial_row(self, parent):
        """串口配置行"""
        frame = ttk.LabelFrame(parent, text="串口", padding=3)
        frame.pack(fill=tk.X, pady=(0, 3))

        ttk.Label(frame, text="端口:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(
            frame, textvariable=self.port_var, width=35, state="readonly"
        )
        self.port_combo.pack(side=tk.LEFT, padx=2)
        ttk.Button(frame, text="刷新", command=self.refresh_ports, width=5).pack(
            side=tk.LEFT
        )

        ttk.Label(frame, text="波特率:").pack(side=tk.LEFT, padx=(10, 2))
        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(
            frame,
            textvariable=self.baud_var,
            width=7,
            state="readonly",
            values=[
                "1200",
                "2400",
                "4800",
                "9600",
                "19200",
                "38400",
                "57600",
                "115200",
            ],
        ).pack(side=tk.LEFT)

        ttk.Label(frame, text="数据位:").pack(side=tk.LEFT, padx=(8, 2))
        self.data_var = tk.StringVar(value="8")
        ttk.Combobox(
            frame,
            textvariable=self.data_var,
            width=3,
            state="readonly",
            values=["5", "6", "7", "8"],
        ).pack(side=tk.LEFT)

        ttk.Label(frame, text="停止位:").pack(side=tk.LEFT, padx=(8, 2))
        self.stop_var = tk.StringVar(value="1")
        ttk.Combobox(
            frame,
            textvariable=self.stop_var,
            width=3,
            state="readonly",
            values=["1", "1.5", "2"],
        ).pack(side=tk.LEFT)

        ttk.Label(frame, text="校验:").pack(side=tk.LEFT, padx=(8, 2))
        self.parity_var = tk.StringVar(value="无")
        ttk.Combobox(
            frame,
            textvariable=self.parity_var,
            width=5,
            state="readonly",
            values=["无", "奇校验", "偶校验"],
        ).pack(side=tk.LEFT)

        self.open_btn = ttk.Button(
            frame, text="打开串口", command=self.toggle_serial, width=10
        )
        self.open_btn.pack(side=tk.LEFT, padx=(12, 5))
        self.port_status_lbl = ttk.Label(frame, text="🔴", foreground="red")
        self.port_status_lbl.pack(side=tk.LEFT)

    def _build_config_row(self, parent):
        """配置 + 轮循控制行"""
        frame = ttk.LabelFrame(parent, text="配置与轮循", padding=3)
        frame.pack(fill=tk.X, pady=(0, 3))

        ttk.Button(frame, text="📂 加载配置", command=self.load_config_dialog).pack(
            side=tk.LEFT
        )
        ttk.Button(frame, text="🔄 重载", command=self.reload_config, width=5).pack(
            side=tk.LEFT, padx=2
        )

        self.config_lbl = ttk.Label(frame, text="未加载配置", foreground="gray")
        self.config_lbl.pack(side=tk.LEFT, padx=5)

        ttk.Separator(frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(frame, text="从站ID:").pack(side=tk.LEFT)
        self.poll_slave_var = tk.StringVar(value="1")
        ttk.Entry(frame, textvariable=self.poll_slave_var, width=4).pack(
            side=tk.LEFT, padx=1
        )

        ttk.Label(frame, text="间隔(ms):").pack(side=tk.LEFT, padx=(8, 2))
        self.poll_interval_var = tk.StringVar(value="1000")
        ttk.Entry(frame, textvariable=self.poll_interval_var, width=6).pack(
            side=tk.LEFT
        )

        self.start_btn = ttk.Button(
            frame, text="▶ 启动", command=self.start_polling, width=8
        )
        self.start_btn.pack(side=tk.LEFT, padx=(10, 2))
        self.stop_btn = ttk.Button(
            frame, text="⏹ 停止", command=self.stop_polling, width=8, state="disabled"
        )
        self.stop_btn.pack(side=tk.LEFT)

        self.poll_status_lbl = ttk.Label(frame, text="● 停止", foreground="red")
        self.poll_status_lbl.pack(side=tk.LEFT, padx=8)

        self.stats_lbl = ttk.Label(
            frame, text="轮询:0 成功:0 失败:0", foreground="gray"
        )
        self.stats_lbl.pack(side=tk.LEFT, padx=5)

        # 写入按钮
        self.write_btn = ttk.Button(
            frame, text="✏ 写入", command=self._do_write, width=8, state="disabled"
        )
        self.write_btn.pack(side=tk.RIGHT, padx=2)

        ttk.Button(frame, text="🗑 清表", command=self.clear_data_table, width=6).pack(
            side=tk.RIGHT, padx=2
        )
        ttk.Button(frame, text="💾 CSV", command=self.export_data, width=6).pack(
            side=tk.RIGHT
        )

    def _build_bottom_row(self, parent):
        """底部：Hex 命令 + 日志"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, side=tk.BOTTOM)

        # 第1小行：Hex 命令
        hex_frame = ttk.Frame(frame)
        hex_frame.pack(fill=tk.X)
        ttk.Label(hex_frame, text="Hex:").pack(side=tk.LEFT)
        self.hex_var = tk.StringVar()
        ttk.Entry(hex_frame, textvariable=self.hex_var, width=50).pack(
            side=tk.LEFT, padx=2, fill=tk.X, expand=True
        )
        ttk.Button(hex_frame, text="发送", command=self.send_hex_command, width=6).pack(
            side=tk.LEFT, padx=1
        )
        ttk.Button(hex_frame, text="+CRC", command=self.auto_add_crc, width=6).pack(
            side=tk.LEFT
        )
        ttk.Button(hex_frame, text="解析", command=self.parse_clipboard, width=6).pack(
            side=tk.LEFT, padx=1
        )
        ttk.Button(
            hex_frame, text="🔍 监控", command=self.open_data_monitor, width=8
        ).pack(side=tk.LEFT, padx=3)

        # 第2小行：状态日志（单行）
        self.msg_var = tk.StringVar(value="就绪")
        ttk.Label(
            frame, textvariable=self.msg_var, foreground="gray", font=("Consolas", 9)
        ).pack(side=tk.BOTTOM, fill=tk.X, pady=(1, 0), anchor=tk.W)

    def _build_status_bar(self):
        """底部状态栏"""
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, side=tk.BOTTOM, padx=5, pady=(0, 3))
        self.port_bar = ttk.Label(
            bar, text="串口: 未连接", foreground="gray", font=("", 8)
        )
        self.port_bar.pack(side=tk.LEFT, padx=5)
        self.poll_bar = ttk.Label(
            bar, text="轮循: 停止", foreground="gray", font=("", 8)
        )
        self.poll_bar.pack(side=tk.LEFT, padx=15)
        self.cfg_bar = ttk.Label(
            bar, text="配置: 未加载", foreground="gray", font=("", 8)
        )
        self.cfg_bar.pack(side=tk.LEFT, padx=5)

    # ==================== 串口 ====================

    def refresh_ports(self):
        ports = self.serial_mgr.refresh_port_list()
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_serial(self):
        if self.serial_mgr.is_open:
            self.close_serial()
        else:
            self.open_serial()

    def open_serial(self):
        dsp = self.port_var.get()
        if not dsp:
            messagebox.showwarning("", "请选择串口")
            return
        port = self.serial_mgr.parse_port_name(dsp)
        try:
            self.serial_mgr.open(
                port=port,
                baudrate=int(self.baud_var.get()),
                bytesize=int(self.data_var.get()),
                stopbits={"1": 1, "1.5": 1.5, "2": 2}.get(self.stop_var.get(), 1),
                parity={"无": "N", "奇校验": "O", "偶校验": "E"}.get(
                    self.parity_var.get(), "N"
                ),
                timeout=0.5,
            )
            self._set_msg(f"串口 {port} 已打开")
        except Exception as e:
            messagebox.showerror("串口错误", str(e))

    def close_serial(self):
        self.serial_mgr.close()
        self._set_msg("串口已关闭")
        self._update_ui_state()

    def _on_serial_received(self, frame: bytes, parsed: dict):
        with self._sync_lock:
            self._sync_response = frame
            self._sync_event.set()

    def _on_serial_error(self, msg: str):
        self._set_msg(f"串口错误: {msg}")

    def _on_serial_status_change(self, is_open: bool):
        if is_open:
            self.open_btn.config(text="关闭串口")
            self.port_status_lbl.config(text="🟢", foreground="green")
            self.port_bar.config(text="串口: 已连接", foreground="green")
        else:
            self.open_btn.config(text="打开串口")
            self.port_status_lbl.config(text="🔴", foreground="red")
            self.port_bar.config(text="串口: 未连接", foreground="gray")
        self._update_ui_state()

    # ==================== 配置 ====================

    def load_config_dialog(self):
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._load_config(path)

    def reload_config(self):
        if self.config_filepath:
            self._load_config(self.config_filepath)

    def _load_config(self, filepath: str):
        try:
            config = load_config(filepath)
            errs = validate_config(config)
            if errs and not messagebox.askyesno(
                "提示", "\n".join(errs) + "\n\n仍要加载？"
            ):
                return

            self.app_config = config
            self.config_filepath = filepath
            self.polling_engine.load_config(config)

            name = os.path.basename(filepath)
            self.config_lbl.config(text=f"✅ {name}", foreground="green")
            self.cfg_bar.config(text=f"配置: {name}", foreground="green")

            # 同步串口参数
            self.baud_var.set(str(config.serial.baudrate))
            self.data_var.set(str(config.serial.databits))
            self.stop_var.set(config.serial.stopbits)
            self.parity_var.set(config.serial.parity)
            self.poll_slave_var.set(str(config.slave_id))
            self.poll_interval_var.set(str(config.polling.interval_ms))

            # 在表中显示协议格式
            self.data_table.load_from_config(config)
            # 填充组导航
            self.group_nav.load_groups(config)
            self.group_nav.select_first()

            total_points = sum(len(g.points) for g in config.groups)
            self._set_msg(
                f"✅ 配置加载: {name} ({len(config.groups)}组 {total_points}个点位)"
            )
            self._update_ui_state()

        except Exception as e:
            messagebox.showerror("错误", f"加载失败: {e}")

    # ==================== 轮循 ====================

    def start_polling(self):
        if not self.serial_mgr.is_open:
            messagebox.showwarning("", "请先打开串口")
            return
        if not self.app_config:
            messagebox.showwarning("", "请先加载配置")
            return

        try:
            self.app_config.slave_id = int(self.poll_slave_var.get())
            self.app_config.polling.interval_ms = int(self.poll_interval_var.get())
        except ValueError:
            messagebox.showerror("", "ID和间隔须为数字")
            return

        self.polling_engine.load_config(self.app_config)
        if self.polling_engine.start():
            self._set_msg("▶ 轮循已启动")
            self._update_ui_state()
        else:
            messagebox.showwarning("", "启动失败，检查配置中是否有启用的组")

    def stop_polling(self):
        self.polling_engine.stop()
        self._set_msg("⏹ 轮循已停止")
        self._update_ui_state()

    def clear_data_table(self):
        self.data_table.clear_all()
        if self.app_config:
            self.data_table.load_from_config(self.app_config)
        self._set_msg("表格已重置")

    def export_data(self):
        vals = self.data_table.get_all_values()
        if not vals:
            messagebox.showinfo("", "没有数据")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            import csv

            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["组名", "点位", "原始值", "转换值", "单位", "状态"])
                for p in vals.values():
                    w.writerow(
                        [
                            p.group_name,
                            p.name,
                            p.hex_str,
                            str(p.converted_value)
                            if p.converted_value is not None
                            else "---",
                            p.unit,
                            p.quality,
                        ]
                    )
            self._set_msg(f"💾 已导出: {path}")
        except Exception as e:
            messagebox.showerror("", f"导出失败: {e}")

    # ---------- 轮循回调 ----------

    def _send_poll_request(self, frame: bytes) -> Optional[bytes]:
        if not self.serial_mgr.is_open:
            return None
        with self._sync_lock:
            self._sync_response = None
            self._sync_event.clear()
        if not self.serial_mgr.send(frame):
            return None
        if self._sync_event.wait(timeout=1.0):
            with self._sync_lock:
                r = self._sync_response
                self._sync_response = None
            return r
        return None

    def _on_poll_result(self, result: GroupPollResult):
        self.root.after(0, lambda: self.data_table.update_from_result(result))
        stats = self.polling_engine.get_stats()
        txt = f"轮询:{stats['total']} 成功:{stats['success']} 失败:{stats['failed']}"
        self.root.after(0, lambda: self.stats_lbl.config(text=txt))
        if result.success:
            self._set_msg(f"✅ {result.group_name}: {len(result.points)}个点更新")
        else:
            self._set_msg(f"❌ {result.group_name}: {result.error_message}")

    def _on_polling_status_change(self, running: bool, msg: str):
        if running:
            self.poll_status_lbl.config(text="● 运行中", foreground="green")
            self.poll_bar.config(text="轮循: 运行中", foreground="green")
        else:
            self.poll_status_lbl.config(text="● 停止", foreground="red")
            self.poll_bar.config(text="轮循: 停止", foreground="gray")
        self._update_ui_state()

    # ==================== 数据监控 ====================

    def open_data_monitor(self):
        """打开底层数据监控窗口"""
        self.data_monitor.show()
        self._set_msg("🔍 数据监控窗口已打开")

    # ==================== 写入操作 ====================

    def _do_write(self):
        """执行写入：从数据表读取编辑的值，构建 0x10 帧发送"""
        if not self.serial_mgr.is_open:
            messagebox.showwarning("", "请先打开串口")
            return
        if not self.app_config or not self._current_write_group:
            return

        group = None
        for g in self.app_config.groups:
            if g.name == self._current_write_group:
                group = g
                break
        if not group or group.function_code != 0x10:
            return

        # 从数据表获取用户编辑的值
        write_vals = self.data_table.get_write_values()
        if not write_vals:
            messagebox.showinfo("", "请先在数据表的「转换值」列双击输入要写入的值")
            return

        # 收集寄存器字节
        register_values = []
        for point in group.points:
            raw_str = write_vals.get(point.name, "0")
            try:
                # 用户输入的是显示值（已转换），需反算回原始寄存器值
                display_val = float(raw_str)
                raw = int((display_val - point.offset) / point.scale)
                if point.signed:
                    if raw < 0:
                        raw += 0x10000
                    elif raw >= 0x8000:
                        raw -= 0x10000
                raw = raw & 0xFFFF
            except (ValueError, ZeroDivisionError):
                messagebox.showerror("", f"'{point.name}' 的值 '{raw_str}' 无效")
                return
            register_values.extend([(raw >> 8) & 0xFF, raw & 0xFF])

        if not register_values:
            return

        try:
            slave_id = int(self.poll_slave_var.get())
            frame = build_modbus_frame(
                slave_id, 0x10, [group.start_address, group.quantity, register_values]
            )
            frame = add_crc(frame)
            hex_str = bytes_to_hex_str(frame)
            self.serial_mgr.send(frame)
            self._set_msg(f"✏ 写入 {group.name}: {hex_str}")
            self.hex_var.set(hex_str)
            self.data_table.clear_write_tags()
        except Exception as e:
            messagebox.showerror("", f"写入失败: {e}")

    # ==================== 组导航回调 ====================

    def _on_group_selected(self, group_name: str):
        """组导航选中某组时，更新数据表"""
        if not self.app_config:
            return
        self.data_table.show_group(self.app_config, group_name)

        # 检查是否是写组
        self._current_write_group = None
        self.write_btn.config(state="disabled")
        for g in self.app_config.groups:
            if g.name == group_name and g.function_code == 0x10:
                self._current_write_group = group_name
                self.write_btn.config(state="normal")
                break

        self._set_msg(f"切换到组: {group_name}")

    # ==================== 手动 Hex 命令 ====================

    def send_hex_command(self):
        s = self.hex_var.get().strip()
        if not s:
            return
        if not self.serial_mgr.is_open:
            messagebox.showwarning("", "请先打开串口")
            return
        try:
            frame = hex_str_to_bytes(s)
            if frame:
                self.serial_mgr.send(frame)
                self._set_msg(f"TX → {bytes_to_hex_str(frame)}")
        except Exception as e:
            self._set_msg(f"格式错误: {e}")

    def auto_add_crc(self):
        s = self.hex_var.get().strip()
        if not s:
            return
        try:
            data = hex_str_to_bytes(s)
            frame = add_crc(data)
            self.hex_var.set(bytes_to_hex_str(frame))
        except Exception as e:
            self._set_msg(f"CRC失败: {e}")

    def parse_clipboard(self):
        try:
            clip = self.root.clipboard_get().strip()
            data = hex_str_to_bytes(clip)
            if len(data) < 4:
                self._set_msg("数据太短")
                return
            resp = parse_modbus_response(data)
            self._show_parse_result(resp)
            self.hex_var.set(bytes_to_hex_str(data))
        except Exception as e:
            self._set_msg(f"解析失败: {e}")

    # ==================== 工具 ====================

    def _set_msg(self, text: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.msg_var.set(f"[{ts}] {text}")

    def _show_parse_result(self, result: dict):
        """弹窗显示解析结果"""
        if "error" in result:
            messagebox.showinfo("解析结果", f"⚠ 错误: {result['error']}")
            return
        lines = [
            f"从站: {result['slave_id']}",
            f"功能码: 0x{result['function']:02X}",
        ]
        if "registers" in result:
            regs = result["registers"]
            lines.append(f"寄存器 ({len(regs)}个):")
            for i, v in enumerate(regs):
                lines.append(f"  [{i}] 0x{v:04X} ({v})")
        messagebox.showinfo("解析结果", "\n".join(lines))

    def _update_ui_state(self):
        is_open = self.serial_mgr.is_open
        is_poll = self.polling_engine.is_running
        has_cfg = self.app_config is not None

        self.start_btn.config(
            state="normal" if (is_open and has_cfg and not is_poll) else "disabled"
        )
        self.stop_btn.config(state="normal" if is_poll else "disabled")
        # 写按钮状态在 _show/_hide_write_panel 中管理

    def on_closing(self):
        self.polling_engine.stop()
        self.serial_mgr.close()
        self.root.destroy()

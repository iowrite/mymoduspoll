"""
串口管理模块
============
封装串口设备的打开、关闭、读写操作，提供回调机制。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from modbus_modules.modbus_core import parse_modbus_response


class SerialManager:
    """
    串口管理器
    管理串口的生命周期，提供数据收发和接收回调功能。
    """

    def __init__(self):
        self.ser: Optional["serial.Serial"] = None
        self._running = False
        self._read_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 回调
        self.on_send: Optional[Callable[[bytes], None]] = None
        self.on_received: Optional[Callable[[bytes, dict], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_status_change: Optional[Callable[[bool], None]] = None

    @property
    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def open(
        self,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 8,
        stopbits: int = 1,
        parity: str = "N",
        timeout: float = 0.5,
    ) -> None:
        """打开串口"""
        import serial as _serial

        if self.is_open:
            self.close()

        try:
            self.ser = _serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                stopbits=stopbits,
                parity=parity,
                timeout=timeout,
            )
            self._running = True
            self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._read_thread.start()

            if self.on_status_change:
                self.on_status_change(True)

        except Exception as e:
            self.ser = None
            raise

    def close(self) -> None:
        """关闭串口"""
        self._running = False
        with self._lock:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None

        if self.on_status_change:
            self.on_status_change(False)

    def send(self, data: bytes) -> bool:
        """发送数据"""
        if not self.is_open:
            return False
        with self._lock:
            try:
                self.ser.write(data)
                if self.on_send:
                    self.on_send(data)
                return True
            except Exception:
                if self.on_error:
                    self.on_error("发送失败")
                return False

    def _read_loop(self) -> None:
        """后台读取线程"""
        buffer = b""
        while self._running:
            try:
                ser = self.ser
                if ser and ser.is_open and ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    if data:
                        buffer += data
                        # 尝试提取完整帧
                        while len(buffer) >= 4:
                            resp = parse_modbus_response(buffer)
                            if (
                                "error" not in resp
                                or resp["error"] == "帧太短 (< 4 bytes)"
                            ):
                                if "error" not in resp:
                                    # 有效帧
                                    if self.on_received:
                                        frame_bytes = buffer[: len(buffer)]
                                        self.on_received(frame_bytes, resp)
                                    buffer = b""
                                    break
                                else:
                                    # 帧太短，等更多数据
                                    break
                            else:
                                if len(buffer) > 256:
                                    buffer = buffer[1:]
                                else:
                                    break
                else:
                    time.sleep(0.05)
            except Exception as e:
                if self._running and self.on_error:
                    self.on_error(f"读取异常: {e}")
                time.sleep(0.1)

    def refresh_port_list(self) -> list[str]:
        """刷新可用串口列表"""
        import serial.tools.list_ports

        ports = serial.tools.list_ports.comports()
        return [f"{p.device} - {p.description}" for p in ports]

    def parse_port_name(self, display: str) -> str:
        """从 'COMxx - desc' 格式中提取 COM 口名"""
        return display.split(" - ")[0] if " - " in display else display

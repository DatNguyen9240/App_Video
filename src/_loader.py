"""
Tiện ích nội bộ: load module theo đường dẫn file
(Tránh lỗi import tên module bắt đầu bằng số)
"""
import importlib.util
import os
import sys

_SRC = os.path.dirname(os.path.abspath(__file__))


def _load(filename: str):
    path = os.path.join(_SRC, filename)
    name = filename.replace(".py", "").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


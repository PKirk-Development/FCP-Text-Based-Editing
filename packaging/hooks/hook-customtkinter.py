"""
PyInstaller hook — customtkinter

Ensures all theme JSON files and assets bundled inside the
customtkinter package are included in the frozen application.

Without this, CustomTkinter raises a FileNotFoundError at runtime
because it can't locate its built-in themes (blue.json, dark-blue.json,
green.json, etc.) after the package is frozen into the archive.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("customtkinter", include_py_files=False)

"""
PyInstaller hook — torch (PyTorch)

Ensures the frozen .app includes:
  • stdlib modules that torch imports at runtime (e.g. unittest)
  • all native shared libraries (.dylib/.so) that torch loads via ctypes,
    including libtorch_global_deps.dylib, libtorch_cpu.dylib, libc10.dylib,
    and any CUDA/MPS libraries — without these the app crashes at startup with:
        OSError: dlopen(…/torch/lib/libtorch_global_deps.dylib): no such file
  • torch data files (ATen kernel registrations, etc.)

Without this hook, WITH_WHISPER builds fail at runtime with:
    ModuleNotFoundError: No module named 'unittest'
    ImportError: openai-whisper is not installed or failed to initialize
    OSError: dlopen(…libtorch_global_deps.dylib …): no such file
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# Native shared libraries (.dylib on macOS, .so on Linux) that torch loads
# via ctypes at startup (torch/__init__.py:_load_global_deps).  PyInstaller
# cannot discover ctypes-loaded libraries from Python import analysis alone,
# so we must collect them explicitly here.
binaries = collect_dynamic_libs("torch")

# Torch data files: ATen op registrations, kernel metadata, etc.
datas = collect_data_files("torch")

# stdlib modules required by torch internals.  PyInstaller may strip them
# when the spec's excludes list is aggressive; listing them here guarantees
# they are always collected for any build that includes torch.
hiddenimports = [
    "unittest",
    "unittest.case",
    "unittest.loader",
    "unittest.main",
    "unittest.mock",
    "unittest.result",
    "unittest.runner",
    "unittest.signals",
    "unittest.suite",
    "unittest.util",
]

# Collect torch submodules but skip tensorboard — it is an optional
# development dependency not required at inference time.  Including it when
# tensorboard is absent produces a noisy build warning and inflates the
# bundle unnecessarily.
hiddenimports += [
    m for m in collect_submodules("torch")
    if not m.startswith("torch.utils.tensorboard")
]

# Explicitly tell PyInstaller not to bundle tensorboard.
excludedimports = ["tensorboard", "torch.utils.tensorboard"]

"""
PyInstaller hook — torch (PyTorch)

Ensures the frozen .app includes stdlib modules that torch imports at runtime
(e.g. torch.utils._config_module imports ``unittest``) and suppresses the
build-time warning about torch.utils.tensorboard when tensorboard is not
installed in the build environment.

Without this hook, WITH_WHISPER builds fail at runtime with:
    ModuleNotFoundError: No module named 'unittest'
    ImportError: openai-whisper is not installed or failed to initialize
"""

from PyInstaller.utils.hooks import collect_submodules

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

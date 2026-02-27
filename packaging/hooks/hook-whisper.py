"""
PyInstaller hook — openai-whisper

Collects all whisper submodules (audio, model, tokenizer, transcribe, utils,
decoding, …) and its data files (mel filter bank assets, tiktoken vocab, etc.)
so that the frozen .app can initialise whisper without hitting import errors at
``whisper/__init__.py`` startup.

Without this hook the Full build fails with:
    File "src/transcriber.py", line 45, in transcribe
    File "pyimod02_importers.py", line 457, in exec_module
    File "whisper/__init__.py", line 8, in <module>
because PyInstaller does not automatically discover dynamically-loaded
submodules or package-internal data files.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Pull in every whisper submodule (audio, model, tokenizer, transcribe, …)
hiddenimports = collect_submodules("whisper")

# Include mel filterbank .npy files, assets/multilingual.tiktoken, etc.
datas = collect_data_files("whisper")

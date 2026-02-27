from setuptools import setup, find_packages

setup(
    name="fcp-text-editor",
    version="1.0.0",
    description="Text-based video editing tool with FCP integration",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "textual>=0.60.0",
        "rich>=13.7.0",
        "click>=8.1.0",
        "pydub>=0.25.1",
        "numpy>=1.24.0",
        "ffmpeg-python>=0.2.0",
    ],
    extras_require={
        "whisper": ["openai-whisper>=20231117"],
    },
    entry_points={
        "console_scripts": [
            "fcp-edit=main:cli",
        ],
    },
)

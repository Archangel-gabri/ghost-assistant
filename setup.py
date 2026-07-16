#!/usr/bin/env python3
"""Setup script for Ghost — voice + screen assistant."""

from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

setup(
    name="ghost-assistant",
    version="2.0.0",
    description="Super-fast AI session helper with speech recognition",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Danya Kubrak",
    author_email="archangel-gabri@users.noreply.github.com",
    url="https://github.com/yourusername/ghost-assistant",
    license="MIT",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    install_requires=[
        "PySide6>=6.5.0",
        "mss>=7.0.0",
        "Pillow>=9.0.0",
        "ImageHash>=4.3.0",
        "numpy>=1.22.0",
        "sounddevice>=0.4.5",
        "faster-whisper>=0.10.0",
        "PyYAML>=6.0",
        "transformers>=4.30.0",
        "torch>=2.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "black>=23.0",
            "flake8>=6.0",
            "mypy>=1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "ghost=ghost.__main__:main",
            "ghost-cli=ghost.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: POSIX :: Linux",
        "Topic :: Multimedia :: Sound/Audio :: Speech",
    ],
    keywords="ai session speech-recognition claude transcription",
    include_package_data=True,
)

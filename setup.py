from setuptools import setup, find_packages

setup(
    name="pdf-autotagger",
    version="1.0.0",
    description="Enterprise PDF Auto-Tagging & Accessibility Remediation Engine (PDF/UA-1, PDF/UA-2, WCAG 2.1/2.2 AA) powered by OpenDataLoader",
    author="Antigravity",
    packages=find_packages(),
    install_requires=[
        "pikepdf>=9.0.0",
        "pymupdf>=1.24.0",
        "pydantic>=2.5.0",
        "opendataloader-pdf>=2.5.0",
        "pillow>=10.0.0",
        "reportlab>=4.0.0",
        "pytest>=7.0.0",
    ],
    entry_points={
        "console_scripts": [
            "pdf-autotag=src.cli:main",
        ],
    },
    python_requires=">=3.10",
)

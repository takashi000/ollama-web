"""Verify a normal pip wheel installation independently of uv.lock and src/."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

_ASSETS = [
    "ollama_web/templates/index.html",
    "ollama_web/static/app.js",
    "ollama_web/static/vendor/purify.min.js",
    "ollama_web/i18n/messages/ja.json",
    "ollama_web/i18n/messages/en.json",
    "ollama_web/prompts/ja.json",
    "ollama_web/prompts/en.json",
]


def check_archives(wheel: Path) -> None:
    """Check shipped metadata and assets before installing the wheel."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert all(asset in names for asset in _ASSETS)
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        python = SpecifierSet(metadata["Requires-Python"])
        assert "3.13" not in python and "3.14" in python and "3.15" in python
        requirements = {
            requirement.name.lower(): requirement.specifier
            for value in metadata.get_all("Requires-Dist", [])
            if not (requirement := Requirement(value)).marker
        }
        for package, vulnerable, fixed in [
            ("pillow", "12.3.0", "13.0.0"),
            ("pypdf", "6.21.1", "6.21.2"),
            ("certifi", "2026.6.17", "2026.10.19"),
        ]:
            assert vulnerable not in requirements[package]
            assert fixed in requirements[package]
    sources = list(wheel.parent.glob("ollama_web-*.tar.gz"))
    assert len(sources) == 1, "Expected exactly one source distribution"
    with tarfile.open(sources[0]) as archive:
        names = archive.getnames()
        assert all(any(name.endswith(f"/src/{asset}") for name in names) for asset in _ASSETS)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    wheels = list((root / "dist" / "security-validation").glob("ollama_web-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one ollama-web wheel in dist/security-validation/.")
    check_archives(wheels[0])

    with tempfile.TemporaryDirectory(prefix="ollama-web-wheel-") as directory:
        environment = Path(directory)
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def run(*args: str) -> None:
            subprocess.run([str(python), *args], cwd=root, check=True)

        run("-m", "pip", "install", f"{wheels[0]}[dev]")
        run("-m", "pip", "check")
        run(
            "-c",
            "from importlib.metadata import version; "
            "from pathlib import Path; import sys, ollama_web; "
            "from packaging.version import Version; "
            "floors = {'anyio': '4.14.2', 'PyJWT': '2.15.1', 'pypdf': '6.21.2', "
            "'mcp': '1.30.0', 'soupsieve': '2.9.0', 'pillow': '13.0.0', "
            "'certifi': '2026.10.19', "
            "'cryptography': '50.0.0'}; "
            "assert Path(ollama_web.__file__).resolve().is_relative_to("
            "Path(sys.prefix).resolve()), (ollama_web.__file__, sys.prefix); "
            "assert all(Version(version(p)) >= Version(v) for p, v in floors.items()); "
            "print({p: version(p) for p in floors})",
        )
        # Browser tests run separately against the locked installation in CI.
        run("-m", "pytest", "tests", "-q", "-m", "not ui", "-o", "pythonpath=")


if __name__ == "__main__":
    main()

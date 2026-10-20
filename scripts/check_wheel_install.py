"""Verify a normal pip wheel installation independently of uv.lock and src/."""

from __future__ import annotations

import os
import subprocess
import tempfile
import venv
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    wheels = list((root / "dist").glob("ollama_web-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one ollama-web wheel in dist/.")

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
            "floors = {'anyio': '4.14.2', 'PyJWT': '2.15.1', 'pypdf': '6.19.0', "
            "'mcp': '1.30.0', 'soupsieve': '2.9.0', 'pillow': '12.3.0', "
            "'cryptography': '50.0.0'}; "
            "assert Path(ollama_web.__file__).resolve().is_relative_to("
            "Path(sys.prefix).resolve()), (ollama_web.__file__, sys.prefix); "
            "assert all(Version(version(p)) >= Version(v) for p, v in floors.items()); "
            "print({p: version(p) for p in floors})",
        )
        run("-m", "pytest", "tests", "-q", "-o", "pythonpath=")


if __name__ == "__main__":
    main()

"""Render docs/architecture.dot to PNG and SVG (requires Graphviz `dot` on PATH)."""
import pathlib
import shutil
import subprocess
import sys

docs = pathlib.Path(__file__).resolve().parent.parent / "docs"
if not shutil.which("dot"):
    sys.exit("Graphviz 'dot' not found. Install it (e.g. apt install graphviz) or paste the Mermaid diagram from README.md into mermaid.live")
for fmt in ("png", "svg"):
    subprocess.run(["dot", f"-T{fmt}", "-Gdpi=150", str(docs / "architecture.dot"), "-o", str(docs / f"architecture.{fmt}")], check=True)
print("wrote docs/architecture.png and .svg")

# PyInstaller spec for a standalone build of the extension bridge.
#
#   pyinstaller firefox_extension/server/tpd-server.spec
#
# Produces dist/tpd-server/ (one-dir: a one-file build would unpack spaCy on
# every start). Build on the target OS; PyInstaller does not cross-compile.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

server_dir = Path(SPECPATH)
repo_root = server_dir.parent.parent

datas, binaries, hiddenimports = [], [], []

# spaCy resolves components through registries and entry points, so take each
# package whole (code, data and dist-info metadata).
for pkg in (
    "spacy", "spacy_legacy", "spacy_loggers", "thinc", "en_core_web_sm",
    "srsly", "blis", "cymem", "preshed", "murmurhash", "catalogue",
    "confection", "weasel", "wasabi", "langcodes", "tldextract",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += collect_data_files("tpd")

a = Analysis(
    [str(server_dir / "server.py")],
    pathex=[str(repo_root), str(server_dir)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    # Optional heavyweights the bridge degrades without: torch only tunes
    # worker threads, and Playwright (probe/JS rendering) needs a browser the
    # bundle does not ship. The rest are optional imports of dependencies
    # (networkx, thinc, ...) that only a full dev environment provides.
    excludes=[
        "torch", "transformers", "setfit", "playwright",
        "spacy_curated_transformers", "curated_transformers",
        "tensorflow", "matplotlib", "IPython", "pytest", "tkinter",
        "pandas", "pyarrow", "scipy", "sklearn", "datasets", "aiohttp",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tpd-server",
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name="tpd-server")

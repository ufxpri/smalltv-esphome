# PyInstaller spec — builds a single-file, windowed SmallTV Widget.
#
#   cd client
#   pip install -r requirements-widget.txt pyinstaller
#   pyinstaller build/smalltv_widget.spec
#
# Output: client/dist/SmallTVWidget(.exe)  or  client/dist/SmallTVWidget.app
import sys

block_cipher = None

a = Analysis(
    ["../smalltv_widget.py"],
    pathex=[".."],                       # so `smalltv` and `widget` are importable
    binaries=[],
    # No gifs/ here on purpose: stickers are user content, and baking them in
    # would mean rebuilding the exe to add one. A frozen build reads them from
    # the config dir instead — see stream.gif_dir().
    datas=[],
    # The panel and the stream sources aren't imported by the widget — they are
    # re-entered through `smalltv_widget.py --run <script>` (see stream.command),
    # so name them explicitly or PyInstaller won't bundle them.
    hiddenimports=[
        "smalltv", "widget", "config", "stream", "marketdata",
        "control_panel", "smalltv_stream",
        "stream_stocks", "stream_sectors", "stream_gif", "stream_video",
        "pystray", "PIL", "psutil", "numpy",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SmallTVWidget",
    debug=False,
    strip=False,
    upx=True,
    console=False,                       # windowed / no terminal
    disable_windowed_traceback=False,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="SmallTVWidget.app",
        icon=None,
        bundle_identifier="com.smalltv.widget",
        info_plist={
            # menu-bar agent: no Dock icon, no app switcher entry
            "LSUIElement": True,
            "CFBundleShortVersionString": "0.1.0",
        },
    )

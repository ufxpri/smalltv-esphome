# PyInstaller spec — builds a windowed SmallTV Widget as a onedir bundle.
#
#   cd client
#   pip install -r requirements-widget.txt pyinstaller
#   pyinstaller build/smalltv_widget.spec
#
# Output: client/dist/SmallTVWidget/SmallTVWidget(.exe)  or  client/dist/SmallTVWidget.app
#
# onedir, not onefile, on purpose: the widget is a resident daemon, and onefile
# unpacks into a temp _MEI dir that the OS may clean up while the process is
# still alive — files re-read at runtime (curl_cffi's CA bundle) then vanish
# and every fetch fails with curl (77). onedir keeps everything at a permanent
# path next to the exe, which also kills the reaped-_MEI child-spawn bug.
import os
import sys

from PyInstaller.utils.hooks import collect_all

# Relative paths in a spec resolve against SPECPATH (this file's dir), not the
# cwd — so `..` here is client/, matching `pathex` and the Analysis script below.
sys.path.insert(0, os.path.abspath(os.path.join(SPECPATH, "..")))
from widget import assets

# The .ico/.icns is rendered here rather than committed — same drawing as the
# tray glyph (widget/assets.py, mirroring widget/icon.svg), so the packaged app
# and the tray can never drift apart.
APP_ICON = assets.write_app_icon(os.path.join(SPECPATH, "generated"))

block_cipher = None

# curl_cffi ships a bundled libcurl + cacert + cffi shims that a bare
# hiddenimport misses; collect_all grabs its binaries/datas too.
_cc_datas, _cc_bins, _cc_hidden = collect_all("curl_cffi")

a = Analysis(
    ["../smalltv_widget.py"],
    pathex=[".."],                       # so `smalltv` and `widget` are importable
    binaries=_cc_bins,
    # No gifs/ here on purpose: stickers are user content, and baking them in
    # would mean rebuilding the exe to add one. A frozen build reads them from
    # the config dir instead — see stream.gif_dir().
    datas=_cc_datas,
    # The panel and the stream sources aren't imported by the widget — they are
    # re-entered through `smalltv_widget.py --run <script>` (see stream.command),
    # so name them explicitly or PyInstaller won't bundle them.
    hiddenimports=[
        "smalltv", "widget", "config", "stream", "marketdata", "claudeusage",
        "control_panel", "smalltv_stream",
        "stream_stocks", "stream_sectors", "stream_claude", "stream_gif", "stream_video",
        "pystray", "PIL", "psutil", "numpy",
    ] + _cc_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SmallTVWidget",
    debug=False,
    strip=False,
    upx=True,
    console=False,                       # windowed / no terminal
    icon=APP_ICON,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="SmallTVWidget",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SmallTVWidget.app",
        icon=APP_ICON,
        bundle_identifier="com.smalltv.widget",
        info_plist={
            # menu-bar agent: no Dock icon, no app switcher entry
            "LSUIElement": True,
            "CFBundleShortVersionString": "0.1.0",
        },
    )

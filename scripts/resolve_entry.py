"""Installed under the Workspace > Scripts menu by install.ps1."""

from pathlib import Path
import sys
import traceback


def main():
    sys.path.insert(0, WORKSPACE_ROOT)
    from davinci_search.runtime import Settings
    from davinci_search.window import open_window

    host = globals().get("resolve")
    dispatcher = globals().get("bmd")
    if dispatcher is None:
        raise RuntimeError("Mở Davinci Semantic Search từ Workspace > Scripts.")
    if not host:
        host = dispatcher.scriptapp("Resolve")
    if not host:
        raise RuntimeError("Không kết nối được với DaVinci Resolve.")
    open_window(host, dispatcher.UIDispatcher, Settings.load(Path(CONFIG_FILE)))


try:
    main()
except Exception:
    details = traceback.format_exc()
    print(details)
    Path(CONFIG_FILE).with_name("resolve-script-error.log").write_text(details, encoding="utf-8")

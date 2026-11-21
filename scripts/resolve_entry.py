"""Installed under the Workspace > Scripts menu by install.ps1."""

from pathlib import Path
import sys
import traceback


def main():
    config = Path(str(globals()["CONFIG_FILE"]))
    diagnostics = None
    step = "imports"
    try:
        sys.path.insert(0, str(globals()["WORKSPACE_ROOT"]))
        from davinci_search.diagnostics import DiagnosticLog

        diagnostics = DiagnosticLog(config.parent)
        step = "resolve_connection"
        host = globals().get("resolve")
        dispatcher = globals().get("bmd")
        if dispatcher is None:
            raise RuntimeError("Mở Davinci Semantic Search từ Workspace > Scripts.")
        if not host:
            host = dispatcher.scriptapp("Resolve")
        if not host:
            raise RuntimeError("Không kết nối được với DaVinci Resolve.")
        try:
            version = host.GetVersion()[:4]
        except Exception:
            version = []
        if version and all(type(part) is int for part in version):
            diagnostics.record("resolve_connected", version=".".join(map(str, version)))

        step = "settings"
        from davinci_search.runtime import Settings

        settings = Settings.load(config)
        step = "window"
        from davinci_search.window import open_window

        open_window(host, dispatcher.UIDispatcher, settings)
    except Exception as error:
        details = traceback.format_exc()
        print(details)
        if diagnostics is not None:
            diagnostics.failure("resolve_error", step, error)
        try:
            config.with_name("resolve-script-error.log").write_text(details, encoding="utf-8")
        except OSError:
            pass


main()

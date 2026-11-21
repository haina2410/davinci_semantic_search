"""Transcript search window launched from Resolve's Workspace menu."""

from __future__ import annotations

from functools import wraps
from subprocess import Popen
from time import monotonic, sleep
from uuid import uuid4

from .diagnostics import export_bundle, open_export_folder
from .hardware import describe_recognition, hardware_fallback, start_hardware_probe
from .index import Index, Media, project_db
from .resolve import read_timeline, scan_media, select_source
from .runtime import Settings
from .search import Hit, search
from .timeline import Snapshot, TimelineHit, add_search_marker, clear_search_markers, project_hits, timecode
from .timeline_audit import audit_timeline
from .ui_text import english_diagnostic
from .worker import STOPPABLE, request_index, request_stop, start_recovery, starting, worker_active


def source_time(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


def open_window(resolve, dispatcher_factory, settings: Settings) -> None:
    ui = resolve.Fusion().UIManager
    dispatch = dispatcher_factory(ui)
    window = dispatch.AddWindow(
        {"ID": "TranscriptSearch", "WindowTitle": "Davinci Semantic Search", "Geometry": [200, 160, 1040, 570]},
        [ui.VGroup({"Spacing": 8}, [
            ui.HGroup({"Weight": 0}, [
                ui.Button({"ID": "Index", "Text": "Index Project", "Weight": 0}),
                ui.Button({"ID": "Stop", "Text": "Stop", "Enabled": False, "Weight": 0}),
                ui.Button({"ID": "Refresh", "Text": "Refresh Progress", "Weight": 0}),
                ui.Label({"ID": "IndexStatus", "Text": "", "Weight": 1}),
                ui.Button({"ID": "About", "Text": "About", "Weight": 0}),
            ]),
            ui.HGroup({"Weight": 0}, [
                ui.ComboBox({"ID": "Scope", "Weight": 0}),
                ui.LineEdit({"ID": "Query", "PlaceholderText": "Search Vietnamese or English dialogue"}),
                ui.CheckBox({"ID": "Original", "Text": "Include raw transcript", "Checked": False, "Weight": 0}),
                ui.Button({"ID": "Search", "Text": "Search", "Weight": 0}),
            ]),
            ui.Tree({"ID": "Results", "AlternatingRowColors": True, "SortingEnabled": False, "Weight": 3}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "Message", "WordWrap": True, "Text": "", "Weight": 1}),
                ui.Button({"ID": "SelectSource", "Text": "Select in Media Pool", "Weight": 0}),
                ui.Button({"ID": "Marker", "Text": "Add Temporary Marker", "Weight": 0}),
            ]),
        ])],
    )
    widgets = window.GetItems()
    about = dispatch.AddWindow(
        {"ID": "SearchAbout", "WindowTitle": "About · Davinci Semantic Search", "Geometry": [280, 220, 650, 420]},
        [ui.VGroup({"Spacing": 12}, [
            ui.TextEdit({"ID": "AboutDetails", "ReadOnly": True, "Weight": 1,
                         "StyleSheet": "font-family: Consolas;"}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "ExportStatus", "Text": "", "WordWrap": True, "Weight": 1}),
                ui.Button({"ID": "ExportLogs", "Text": "Export logs", "Weight": 0}),
                ui.Button({"ID": "Done", "Text": "Close", "Weight": 0}),
            ]),
        ])],
    )
    about.Hide()
    about_widgets = about.GetItems()
    scope = widgets["Scope"]
    scope.AddItem("Source")
    scope.AddItem("Timeline")
    tree = widgets["Results"]
    tree.ColumnCount = 7
    header = tree.NewItem()
    columns = (
        ("Scope", 125), ("Source Media", 185), ("Source Time", 110), ("Timeline", 145),
        ("Dialogue", 298), ("Transcript", 128), ("", 0),
    )
    for index, (label, _) in enumerate(columns):
        header.Text[index] = label
    tree.SetHeaderItem(header)
    for index, (_, width) in enumerate(columns):
        tree.ColumnWidth[index] = width
    hide_column = getattr(tree, "SetColumnHidden", None)
    if callable(hide_column):
        hide_column(6, True)

    manager = resolve.GetProjectManager()
    current_index: Index | None = None
    controlled: tuple[Index, str] | None = None
    recovery: Popen[bytes] | None = None
    cleanup_pending = False
    next_cleanup = 0.0
    hardware: Popen[str] | None = None
    hardware_deadline = 0.0
    hardware_text = "Checking system information and dependencies…"
    resolve_status = f"{'Resolve':18} Connected · {resolve.GetProductName() or 'DaVinci Resolve'}"
    recognition_text = describe_recognition(None, False)
    closed = False
    snapshot: Snapshot | None = None
    search_project_id = ""
    search_id = ""
    rows: dict[str, tuple[Hit, TimelineHit | None]] = {}
    message = widgets["Message"]

    def get_project_index():
        nonlocal current_index, recovery, cleanup_pending, next_cleanup
        project = manager.GetCurrentProject()
        if not project:
            raise RuntimeError("Open a DaVinci Resolve project first.")
        path = project_db(settings.data_dir, project.GetUniqueId())
        if current_index is None or current_index.path != path:
            if controlled:
                bound, token = controlled
                run = bound.run_status()
                if run and run.token == token and (worker_active(bound) or starting(run)):
                    raise RuntimeError("This window is still indexing the previous project. "
                                       "Stop indexing or wait for it to finish.")
            current_index = Index(path)
            window.WindowTitle = f"Davinci Semantic Search · {project.GetName()}"
            recovery, cleanup_pending, next_cleanup = None, False, 0.0
            tree.Clear()
            rows.clear()
        return project, current_index

    def handle_errors(function):
        @wraps(function)
        def on_event(event=None):
            try:
                function()
            except Exception as error:
                message.Text = english_diagnostic(str(error)) or type(error).__name__
        return on_event

    def update_about() -> None:
        details = about_widgets["AboutDetails"]
        text = f"{resolve_status}\n{hardware_text}\n\n{recognition_text}"
        if details.PlainText != text:
            details.PlainText = text

    def update_progress(index: Index) -> None:
        nonlocal controlled, recovery, cleanup_pending, next_cleanup, recognition_text
        run = index.run_status()
        if recovery is not None and recovery.poll() is not None:
            cleanup_pending = recovery.returncode != 0
            recovery = None
            next_cleanup = monotonic() + (3 if cleanup_pending else 30)
        running = starting(run) or (recovery is None and worker_active(index))
        stopping = running and run is not None and run.state == "stopping"
        cancelable = running and run is not None and run.state in STOPPABLE
        controlled = (index, run.token) if run and (cancelable or stopping) else None

        if cancelable or stopping:
            next_cleanup = 0.0
        elif not running and recovery is None and monotonic() >= next_cleanup:
            recovery = start_recovery(index, settings)

        status = index.status()
        widgets["Index"].Enabled = not running and recovery is None
        widgets["Stop"].Enabled = cancelable
        widgets["Index"].Text = "Resume Indexing" if status.needs_attention else "Index Project"
        names = {"asr": "awaiting transcription", "polish": "awaiting Codex", "done": "polished",
                 "silent": "without dialogue", "offline": "offline"}
        counts = " · ".join(f"{count} {names[stage]}" for stage, count in status.phases.items())
        if stopping:
            activity = "Stopping"
        elif running:
            activity = "Indexing"
        elif run and run.state == "stopped":
            activity = "Stopped"
        elif status.requested:
            activity = "Previous run incomplete"
        elif status.errors:
            item_count = len(status.errors)
            items = "item" if item_count == 1 else "items"
            activity = f"Retry needed for {item_count} {items}"
        else:
            activity = "Index"
        if cleanup_pending:
            activity += " · Temporary files awaiting cleanup"
        widgets["IndexStatus"].Text = f"{activity} · {counts}" if counts else activity
        widgets["IndexStatus"].ToolTip = "\n".join(
            f"{name} — {english_diagnostic(detail)}" for name, detail in status.errors[:10]
        )
        recognition_text = describe_recognition(run, running and not stopping)
        update_about()

    def update_hardware():
        nonlocal hardware, hardware_text
        if hardware is None:
            return
        if hardware.poll() is None:
            if monotonic() < hardware_deadline:
                return
            hardware.kill()
        result, _ = hardware.communicate()
        hardware_text = (result.strip() if hardware.returncode == 0 else "") or hardware_fallback(settings)
        hardware = None
        update_about()

    @handle_errors
    def refresh():
        _, index = get_project_index()
        update_progress(index)

    @handle_errors
    def start_index():
        project, index = get_project_index()
        if request_index(index, settings, lambda: scan_media(project)):
            message.Text = ""
        else:
            message.Text = "This project is already being indexed."
        update_progress(index)

    @handle_errors
    def poll():
        if current_index is not None:
            update_progress(current_index)
        update_hardware()

    @handle_errors
    def stop():
        if controlled:
            index, token = controlled
            request_stop(index, token)
            update_progress(index)

    @handle_errors
    def close():
        nonlocal closed
        try:
            if controlled:
                request_stop(*controlled)
        finally:
            closed = True
            dispatch.ExitLoop()

    @handle_errors
    def open_about():
        nonlocal hardware, hardware_deadline, hardware_text
        if hardware is None:
            hardware_text = "Checking system information and dependencies…"
            try:
                hardware = start_hardware_probe(settings)
                hardware_deadline = monotonic() + 8
            except OSError:
                hardware_text = hardware_fallback(settings)
        update_about()
        about.Show()
        about.Raise()
        poll()

    def close_about(event=None):
        about.Hide()

    def export_logs(event=None):
        status = about_widgets["ExportStatus"]
        try:
            project = manager.GetCurrentProject() if manager else None
            project_id = project.GetUniqueId() if project else ""
            key = project_db(settings.data_dir, project_id).parent.name if project_id else ""
            path = export_bundle(settings.config_file.parent, key, python=settings.python)
        except Exception:
            status.Text = "Could not export logs. Check that the installation folder is writable."
            return
        status.Text = f"Exported {path.name}"
        try:
            open_export_folder(path)
        except OSError:
            status.Text = f"Exported to {path.parent}"

    def append_result(section: str, hit: Hit, result: TimelineHit | None = None) -> None:
        identifier = str(len(rows))
        rows[identifier] = (hit, result)
        destination = ""
        if result and snapshot:
            destination = (
                f"A{result.placement.track} · "
                f"{timecode(result.frame, snapshot.frame_rate, snapshot.drop_frame)}"
            )
        version = {"pending": "Awaiting polish", "raw": "Raw", "polished": "Polished"}
        row = tree.NewItem()
        for position, value in enumerate((
            section, hit.media.name, source_time(hit.start_ms), destination,
            " ".join(hit.excerpt.split()), version[hit.version], identifier,
        )):
            row.Text[position] = value
        tree.AddTopLevelItem(row)

    def inspect_timeline(timeline) -> Snapshot:
        return read_timeline(timeline, audit_timeline(resolve, timeline, settings.data_dir))

    @handle_errors
    def find():
        nonlocal snapshot, search_id, search_project_id
        project, index = get_project_index()
        query = widgets["Query"].Text.strip()
        if not query:
            message.Text = "Enter dialogue to search for."
            return
        only_timeline = scope.CurrentIndex == 1
        timeline = project.GetCurrentTimeline()
        if only_timeline and not timeline:
            raise RuntimeError("Open a timeline to search its dialogue.")
        update_progress(index)
        hits = search(index.searchable(), query, widgets["Original"].Checked)
        snapshot = inspect_timeline(timeline) if only_timeline and hits else None
        failed_cleanup = clear_search_markers(timeline) if timeline else 0

        tree.Clear()
        rows.clear()
        search_project_id = project.GetUniqueId()
        search_id = uuid4().hex
        if snapshot:
            results = project_hits(snapshot, hits)
            for result in results.inside:
                append_result("In Timeline", result.hit, result)
            for hit in results.outside:
                append_result("Outside Timeline", hit)
            for hit in results.unverified:
                append_result("Unverified", hit)
        elif not only_timeline:
            for hit in hits:
                append_result("Source", hit)
        result_count = len(rows)
        result_label = "result" if result_count == 1 else "results"
        message.Text = f"{result_count} {result_label}" if result_count else "No matching dialogue found."
        if failed_cleanup:
            markers = "marker" if failed_cleanup == 1 else "markers"
            message.Text += f" · Could not remove {failed_cleanup} temporary {markers}."

    def selected() -> tuple[Hit, TimelineHit | None]:
        selection = tree.SelectedItems()
        if not selection:
            raise RuntimeError("Select a search result.")
        selected_rows = selection.values() if isinstance(selection, dict) else selection
        row = next(iter(selected_rows), None)
        result = rows.get(row.Text[6]) if row is not None else None
        if result is None:
            raise RuntimeError("Select a search result.")
        return result

    @handle_errors
    def choose_source():
        project, _ = get_project_index()
        hit, _ = selected()
        if project.GetUniqueId() == search_project_id and select_source(project, hit.media.key):
            message.Text = f"Selected {hit.media.name} · Source time {source_time(hit.start_ms)}."
        else:
            message.Text = "This media is no longer in the project. Search again."

    @handle_errors
    def mark():
        project, _ = get_project_index()
        hit, result = selected()
        if result is None or snapshot is None:
            raise RuntimeError("Select a verified result from In Timeline.")
        timeline = project.GetCurrentTimeline()
        changed_source = Media.from_path(hit.media.key, hit.media.name, hit.media.path).revision != hit.media.revision
        if (project.GetUniqueId() != search_project_id or not timeline or changed_source
                or inspect_timeline(timeline) != snapshot):
            raise RuntimeError("The result's position may have changed. Search again before adding a marker.")
        state = add_search_marker(timeline, snapshot, result, search_id)
        message.Text = {
            "added": "Added a temporary marker to the timeline ruler.",
            "exists": "This search already marked that frame.",
            "occupied": "This frame already has a marker.",
            "stale": "The timeline has changed. Search again before adding a marker.",
            "failed": "Resolve could not add a marker at this frame.",
        }[state]

    window.On.Index.Clicked = start_index
    window.On.Stop.Clicked = stop
    window.On.Refresh.Clicked = refresh
    window.On.About.Clicked = open_about
    window.On.Query.ReturnPressed = find
    window.On.Search.Clicked = find
    window.On.SelectSource.Clicked = choose_source
    window.On.Marker.Clicked = mark
    window.On.TranscriptSearch.Close = close
    about.On.SearchAbout.Close = close_about
    about.On.Done.Clicked = close_about
    about.On.ExportLogs.Clicked = export_logs
    refresh()
    window.Show()
    next_progress = 0.0
    try:
        while not closed:
            monitoring = bool(controlled or recovery or cleanup_pending or hardware)
            dispatch.StepLoop(not monitoring)
            if monitoring and not closed:
                now = monotonic()
                if now >= next_progress:
                    poll()
                    next_progress = now + 0.6
                sleep(0.05)
    finally:
        if hardware is not None:
            hardware.kill()
            hardware.communicate()
        about.Hide()
        window.Hide()

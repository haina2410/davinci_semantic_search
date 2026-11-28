"""Transcript search window launched from Resolve's Workspace menu."""

from __future__ import annotations

from functools import wraps
import json
import os
from pathlib import Path
import subprocess
from subprocess import Popen
from time import monotonic, sleep

from .diagnostics import export_bundle, open_export_folder
from .gpu import load_gpu_preference, save_gpu_preference
from .hardware import describe_recognition, hardware_fallback, start_hardware_probe
from .index import Index, IndexStatus, Media, project_db
from .resolve import read_timeline, scan_media, select_source, source_timecode
from .runtime import Settings
from .search import Hit, search
from .timeline import Snapshot, TimelineHit, clear_search_markers, mark_search_result, project_hits, timecode
from .timeline_audit import audit_timeline, canonical_path
from .ui_text import GPU_RESUME_ADVICE, empty_search_message, english_diagnostic
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
                ui.ComboBox({"ID": "Scope", "Events": {"CurrentIndexChanged": True}, "Weight": 0}),
                ui.LineEdit({"ID": "Query", "PlaceholderText": "Search Vietnamese or English dialogue"}),
                ui.Button({"ID": "Search", "Text": "Search", "Weight": 0}),
            ]),
            ui.Tree({"ID": "Results", "AlternatingRowColors": True, "SortingEnabled": False,
                     "Events": {"ItemSelectionChanged": True}, "Weight": 3}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "Message", "WordWrap": True, "Text": "", "Weight": 1}),
                ui.Button({"ID": "SelectSource", "Text": "Open at Source Time", "Weight": 0}),
                ui.Button({"ID": "CopySourceTimecode", "Text": "Copy Source Timecode", "Visible": False, "Weight": 0}),
                ui.Button({"ID": "Marker", "Text": "Add Temporary Marker", "Visible": False, "Weight": 0}),
                ui.Button({"ID": "ClearMarkers", "Text": "Clear Temporary Markers", "Visible": False, "Weight": 0}),
            ]),
        ])],
    )
    widgets = window.GetItems()
    about = dispatch.AddWindow(
        {"ID": "SearchAbout", "WindowTitle": "About · Davinci Semantic Search", "Geometry": [280, 220, 650, 420]},
        [ui.VGroup({"Spacing": 12}, [
            ui.HGroup({"Visible": bool(settings.whisper_vulkan), "Weight": 0}, [
                ui.Label({"Text": "Transcription GPU", "Weight": 0}),
                ui.ComboBox({"ID": "TranscriptionGpu", "Enabled": False,
                             "Events": {"CurrentIndexChanged": True}, "Weight": 1}),
            ]),
            ui.TextEdit({"ID": "AboutDetails", "ReadOnly": True, "Weight": 1,
                         "StyleSheet": "font-family: Consolas;"}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "AboutStatus", "Text": "", "WordWrap": True, "Weight": 1}),
                ui.Button({"ID": "ExportLogs", "Text": "Export logs", "Weight": 0}),
                ui.Button({"ID": "Done", "Text": "Close", "Weight": 0}),
            ]),
        ])],
    )
    about.Hide()
    about_widgets = about.GetItems()
    gpu_picker = about_widgets["TranscriptionGpu"]
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
    gpu_names: list[str] = []
    gpu_choices: list[str | None] = []
    selected_gpu: str | None = None
    resolve_status = f"{'Resolve':18} Connected · {resolve.GetProductName() or 'DaVinci Resolve'}"
    recognition_text = describe_recognition(None, False)
    closed = False
    snapshot: Snapshot | None = None
    search_project_id = ""
    rows: dict[str, tuple[Hit, TimelineHit | None]] = {}
    copy_target: tuple[Hit, str] | None = None
    message = widgets["Message"]

    def clear_results():
        nonlocal copy_target, snapshot, search_project_id
        copy_target = None
        snapshot = None
        search_project_id = ""
        rows.clear()
        tree.Clear()
        update_result_actions()

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
                    raise RuntimeError("Indexing is running for the previous project. "
                                       "Reopen the search window to work in the current project.")
            current_index = Index(path)
            window.WindowTitle = f"Davinci Semantic Search · {project.GetName()}"
            recovery, cleanup_pending, next_cleanup = None, False, 0.0
            clear_results()
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

    def update_gpu_picker() -> None:
        gpu_picker.Enabled = False
        gpu_picker.Clear()
        gpu_choices[:] = [None, *gpu_names]
        gpu_picker.AddItem("Auto")
        for name in gpu_names:
            gpu_picker.AddItem(name)
        if selected_gpu and selected_gpu not in gpu_names:
            gpu_choices.append(selected_gpu)
            gpu_picker.AddItem(f"{selected_gpu} (unavailable)")
        gpu_picker.CurrentIndex = gpu_choices.index(selected_gpu)
        gpu_picker.Enabled = len(gpu_choices) > 1

    def change_gpu(event=None) -> None:
        nonlocal selected_gpu
        choice = gpu_picker.CurrentIndex
        if not gpu_picker.Enabled or not 0 <= choice < len(gpu_choices):
            return
        name = gpu_choices[choice]
        if name == selected_gpu:
            return
        status = about_widgets["AboutStatus"]
        try:
            save_gpu_preference(settings.config_file, name)
        except OSError:
            update_gpu_picker()
            status.Text = "Could not save the transcription GPU preference."
            return
        selected_gpu = name
        update_gpu_picker()
        status.Text = "GPU preference saved for the next indexing run."

    def update_progress(index: Index) -> tuple[IndexStatus, bool]:
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
        if run and run.state == "gpu_interrupted":
            widgets["IndexStatus"].Text = f"GPU error · {GPU_RESUME_ADVICE}"
            widgets["IndexStatus"].ToolTip = recognition_text
        update_about()
        return status, running and not stopping

    def update_hardware():
        nonlocal hardware, hardware_text
        if hardware is None:
            return
        if hardware.poll() is None:
            if monotonic() < hardware_deadline:
                return
            hardware.kill()
        result, _ = hardware.communicate()
        gpu_names.clear()
        try:
            details = json.loads(result) if hardware.returncode == 0 else None
        except ValueError:
            details = None
        hardware_text = details["text"] if details else hardware_fallback(settings)
        if details:
            gpu_names.extend(details["gpu_names"])
        hardware = None
        update_gpu_picker()
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
        closed = True
        dispatch.ExitLoop()

    @handle_errors
    def open_about():
        nonlocal hardware, hardware_deadline, hardware_text, selected_gpu
        if hardware is None:
            hardware_text = "Checking system information and dependencies…"
            gpu_picker.Enabled = False
            if settings.whisper_vulkan:
                try:
                    selected_gpu = load_gpu_preference(settings.config_file)
                except (OSError, ValueError):
                    selected_gpu = None
                    about_widgets["AboutStatus"].Text = "Could not read the transcription GPU preference."
            try:
                hardware = start_hardware_probe(settings)
                hardware_deadline = monotonic() + 8
            except OSError:
                hardware_text = hardware_fallback(settings)
                gpu_names.clear()
                update_gpu_picker()
        update_about()
        about.Show()
        about.Raise()
        poll()

    def close_about(event=None):
        about.Hide()

    def export_logs(event=None):
        status = about_widgets["AboutStatus"]
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
        version = {"pending": "Awaiting polish", "polished": "Polished"}
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
        nonlocal snapshot, search_project_id
        clear_results()
        query = widgets["Query"].Text.strip()
        if not query:
            message.Text = "Enter dialogue to search for."
            return
        project, index = get_project_index()
        only_timeline = scope.CurrentIndex == 1
        timeline = project.GetCurrentTimeline() if only_timeline else None
        if only_timeline and not timeline:
            raise RuntimeError("Open a timeline to search its dialogue.")
        status, indexing = update_progress(index)
        transcripts = index.searchable()
        hits = search(transcripts, query)
        if not hits:
            message.Text = empty_search_message(status, has_transcripts=bool(transcripts), indexing=indexing)
            return

        snapshot = inspect_timeline(timeline) if timeline else None
        search_project_id = project.GetUniqueId()
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
        message.Text = f"{result_count} {result_label}"

    def change_scope(event=None):
        if widgets["Query"].Text.strip():
            find()
        else:
            clear_results()
            message.Text = ""

    def selected_result() -> tuple[Hit, TimelineHit | None] | None:
        selection = tree.SelectedItems()
        if not selection:
            return None
        selected_rows = selection.values() if isinstance(selection, dict) else selection
        row = next(iter(selected_rows), None)
        return rows.get(row.Text[6]) if row is not None else None

    def selected() -> tuple[Hit, TimelineHit | None]:
        result = selected_result()
        if result is None:
            raise RuntimeError("Select a search result.")
        return result

    def update_result_actions(event=None):
        selection = selected_result()
        timeline_scope = scope.CurrentIndex == 1
        widgets["Marker"].Visible = bool(timeline_scope and selection and selection[1] is not None)
        widgets["ClearMarkers"].Visible = timeline_scope
        widgets["CopySourceTimecode"].Visible = bool(copy_target and selection and selection[0] is copy_target[0])

    @handle_errors
    def choose_source():
        nonlocal copy_target
        copy_target = None
        update_result_actions()
        project, _ = get_project_index()
        hit, _ = selected()
        if project.GetUniqueId() != search_project_id:
            raise RuntimeError("The project has changed. Search again.")
        if resolve.GetCurrentPage() != "edit":
            resolve.OpenPage("edit")
        clip = select_source(project, hit.media.key)
        if clip is None:
            raise RuntimeError("Resolve could not select this clip in the Media Pool. Search again.")

        properties = clip.GetClipProperty()
        path = properties.get("File Path") if isinstance(properties, dict) else None
        if isinstance(path, str) and (
            canonical_path(path) != canonical_path(hit.media.path)
            or Media.from_path(hit.media.key, hit.media.name, path).revision != hit.media.revision
        ):
            raise RuntimeError("This media has changed or is offline. Search again after reindexing.")
        target = source_timecode(properties, hit.start_ms) if isinstance(properties, dict) else None
        if target is None:
            message.Text = (f"Selected {hit.media.name} · Resolve could not determine a viewer timecode. "
                            f"Dialogue starts {source_time(hit.start_ms)} into the clip.")
            return
        copy_target = (hit, target)
        update_result_actions()
        message.Text = (f"Selected {hit.media.name} · Playhead was not moved. "
                        f"Copy {target} and paste it into the Source Viewer.")

    @handle_errors
    def copy_source_timecode():
        project, _ = get_project_index()
        hit, _ = selected()
        if project.GetUniqueId() != search_project_id or copy_target is None or hit is not copy_target[0]:
            raise RuntimeError("Open the selected result to get its source timecode.")
        target = copy_target[1]
        clipboard = Path(os.environ["SystemRoot"]) / "System32" / "clip.exe"
        try:
            subprocess.run([str(clipboard)], input=target.encode("ascii"), check=True, timeout=2,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError(f"Could not copy timecode. Enter {target} in the Source Viewer.") from None
        message.Text = f"Copied {target}. Paste it into the Source Viewer timecode field."

    @handle_errors
    def mark():
        project, _ = get_project_index()
        hit, result = selected()
        if scope.CurrentIndex != 1 or result is None or snapshot is None:
            raise RuntimeError("Select a verified result from In Timeline.")
        timeline = project.GetCurrentTimeline()
        changed_source = Media.from_path(hit.media.key, hit.media.name, hit.media.path).revision != hit.media.revision
        if (project.GetUniqueId() != search_project_id or not timeline or changed_source
                or inspect_timeline(timeline) != snapshot):
            raise RuntimeError("The result's position may have changed. Search again before adding a marker.")
        if resolve.GetCurrentPage() != "edit":
            resolve.OpenPage("edit")
        state, moved = mark_search_result(timeline, snapshot, result)
        message.Text = {
            "added": "Added a temporary marker to the timeline ruler.",
            "exists": "This frame already has a temporary marker.",
            "occupied": "This frame already has a marker.",
            "stale": "The timeline has changed. Search again before adding a marker.",
            "failed": "Resolve could not add a marker at this frame.",
        }[state]
        if state in ("added", "exists", "occupied"):
            message.Text += " Moved the playhead to the marker." if moved else " Resolve could not move the playhead."

    @handle_errors
    def clear_markers():
        project, _ = get_project_index()
        timeline = project.GetCurrentTimeline()
        if not timeline:
            raise RuntimeError("Open a timeline to clear its temporary markers.")
        removed, failed = clear_search_markers(timeline)
        if not removed and not failed:
            message.Text = "No temporary markers on this timeline."
            return
        message.Text = f"Removed {removed} temporary {'marker' if removed == 1 else 'markers'}."
        if failed:
            message.Text += f" Could not remove {failed}."

    window.On.Index.Clicked = start_index
    window.On.Stop.Clicked = stop
    window.On.Refresh.Clicked = refresh
    window.On.About.Clicked = open_about
    window.On.Query.ReturnPressed = find
    window.On.Search.Clicked = find
    window.On.Scope.CurrentIndexChanged = change_scope
    window.On.Results.ItemSelectionChanged = update_result_actions
    window.On.SelectSource.Clicked = choose_source
    window.On.CopySourceTimecode.Clicked = copy_source_timecode
    window.On.Marker.Clicked = mark
    window.On.ClearMarkers.Clicked = clear_markers
    window.On.TranscriptSearch.Close = close
    about.On.SearchAbout.Close = close_about
    about.On.Done.Clicked = close_about
    about.On.ExportLogs.Clicked = export_logs
    about.On.TranscriptionGpu.CurrentIndexChanged = change_gpu
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

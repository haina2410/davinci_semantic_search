"""Transcript search window launched from Resolve's Workspace menu."""

from __future__ import annotations

from functools import wraps
from subprocess import Popen
from time import monotonic, sleep
from uuid import uuid4

from .hardware import describe_recognition, hardware_fallback, start_hardware_probe
from .index import Index, Media, project_db
from .resolve import read_timeline, scan_media, select_source
from .runtime import Settings
from .search import Hit, search
from .timeline import Snapshot, TimelineHit, add_search_marker, clear_search_markers, project_hits, timecode
from .timeline_audit import audit_timeline
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
                ui.Button({"ID": "Index", "Text": "Lập chỉ mục project", "Weight": 0}),
                ui.Button({"ID": "Stop", "Text": "Dừng", "Enabled": False, "Weight": 0}),
                ui.Button({"ID": "Refresh", "Text": "Cập nhật tiến độ", "Weight": 0}),
                ui.Label({"ID": "IndexStatus", "Text": "", "Weight": 1}),
                ui.Button({"ID": "About", "Text": "About", "Weight": 0}),
            ]),
            ui.HGroup({"Weight": 0}, [
                ui.ComboBox({"ID": "Scope", "Weight": 0}),
                ui.LineEdit({"ID": "Query", "PlaceholderText": "Nhập lời thoại tiếng Việt hoặc tiếng Anh"}),
                ui.CheckBox({"ID": "Original", "Text": "Tìm thêm trong bản gốc", "Checked": False, "Weight": 0}),
                ui.Button({"ID": "Search", "Text": "Tìm", "Weight": 0}),
            ]),
            ui.Tree({"ID": "Results", "AlternatingRowColors": True, "SortingEnabled": False, "Weight": 3}),
            ui.HGroup({"Weight": 0}, [
                ui.Label({"ID": "Message", "WordWrap": True, "Text": "", "Weight": 1}),
                ui.Button({"ID": "SelectSource", "Text": "Chọn trong Media Pool", "Weight": 0}),
                ui.Button({"ID": "Marker", "Text": "Đặt marker tạm", "Weight": 0}),
            ]),
        ])],
    )
    widgets = window.GetItems()
    about = dispatch.AddWindow(
        {"ID": "SearchAbout", "WindowTitle": "About · Davinci Semantic Search", "Geometry": [280, 220, 650, 365]},
        [ui.VGroup({"Spacing": 12}, [
            ui.Label({"ID": "Hardware", "Text": "Đang đọc cấu hình máy…", "WordWrap": True, "Weight": 1,
                      "StyleSheet": "font-family: Consolas;"}),
            ui.Label({"ID": "Recognition", "Text": "Chưa có lượt nhận dạng.", "WordWrap": True, "Weight": 0}),
            ui.Button({"ID": "Done", "Text": "Đóng", "Weight": 0}),
        ])],
    )
    about.Hide()
    about_widgets = about.GetItems()
    scope = widgets["Scope"]
    scope.AddItem("Nguồn")
    scope.AddItem("Timeline")
    tree = widgets["Results"]
    tree.ColumnCount = 7
    header = tree.NewItem()
    columns = (
        ("Phạm vi", 125), ("Media nguồn", 185), ("Mốc nguồn", 110), ("Timeline", 145),
        ("Lời thoại", 298), ("Transcript", 128), ("", 0),
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
    hardware_read = False
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
            raise RuntimeError("Mở một project DaVinci Resolve trước.")
        path = project_db(settings.data_dir, project.GetUniqueId())
        if current_index is None or current_index.path != path:
            if controlled:
                bound, token = controlled
                run = bound.run_status()
                if run and run.token == token and (worker_active(bound) or starting(run)):
                    raise RuntimeError("Cửa sổ đang điều khiển lượt lập chỉ mục của project trước. "
                                       "Hãy dừng lượt đó hoặc đợi nó hoàn tất.")
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
                message.Text = str(error) or type(error).__name__
        return on_event

    def update_progress(index: Index) -> None:
        nonlocal controlled, recovery, cleanup_pending, next_cleanup
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
        widgets["Index"].Text = "Tiếp tục lập chỉ mục" if status.needs_attention else "Lập chỉ mục project"
        names = {"asr": "chờ nhận dạng", "polish": "chờ Codex", "done": "đã hiệu đính",
                 "silent": "không có lời thoại", "offline": "media ngoại tuyến"}
        counts = " · ".join(f"{count} {names[stage]}" for stage, count in status.phases.items())
        if stopping:
            activity = "Đang dừng"
        elif running:
            activity = "Đang lập chỉ mục"
        elif run and run.state == "stopped":
            activity = "Đã dừng"
        elif status.requested:
            activity = "Lượt trước chưa hoàn tất"
        elif status.errors:
            activity = f"{len(status.errors)} media cần thử lại"
        else:
            activity = "Chỉ mục"
        if cleanup_pending:
            activity += " · Còn file tạm chờ dọn"
        widgets["IndexStatus"].Text = f"{activity} · {counts}" if counts else activity
        widgets["IndexStatus"].ToolTip = "\n".join(
            f"{name} — {detail}" for name, detail in status.errors[:10]
        )
        about_widgets["Recognition"].Text = describe_recognition(run, running and not stopping)

    def update_hardware():
        nonlocal hardware, hardware_read
        if hardware is None:
            return
        if hardware.poll() is None:
            if monotonic() < hardware_deadline:
                return
            hardware.kill()
        result, _ = hardware.communicate()
        about_widgets["Hardware"].Text = (result.strip() if hardware.returncode == 0 else "") or hardware_fallback(settings)
        hardware_read = True
        hardware = None

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
            message.Text = "Project này đã có lượt lập chỉ mục đang chạy."
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
        nonlocal hardware, hardware_deadline, hardware_read
        if not hardware_read and hardware is None:
            try:
                hardware = start_hardware_probe(settings)
                hardware_deadline = monotonic() + 8
            except OSError:
                about_widgets["Hardware"].Text = hardware_fallback(settings)
                hardware_read = True
        about.Show()
        about.Raise()
        poll()

    def close_about(event=None):
        about.Hide()

    def append_result(section: str, hit: Hit, result: TimelineHit | None = None) -> None:
        identifier = str(len(rows))
        rows[identifier] = (hit, result)
        destination = ""
        if result and snapshot:
            destination = (
                f"A{result.placement.track} · "
                f"{timecode(result.frame, snapshot.frame_rate, snapshot.drop_frame)}"
            )
        version = {"pending": "Chưa hiệu đính", "raw": "Bản gốc", "polished": "Đã hiệu đính"}
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
            message.Text = "Nhập lời thoại cần tìm."
            return
        only_timeline = scope.CurrentIndex == 1
        timeline = project.GetCurrentTimeline()
        if only_timeline and not timeline:
            raise RuntimeError("Mở timeline cần tìm lời thoại.")
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
                append_result("Trong timeline", result.hit, result)
            for hit in results.outside:
                append_result("Ngoài timeline", hit)
            for hit in results.unverified:
                append_result("Chưa xác minh", hit)
        elif not only_timeline:
            for hit in hits:
                append_result("Nguồn", hit)
        message.Text = f"{len(rows)} kết quả" if rows else "Không tìm thấy lời thoại khớp."
        if failed_cleanup:
            message.Text += f" · Không dọn được {failed_cleanup} marker tạm."

    def selected() -> tuple[Hit, TimelineHit | None]:
        selection = tree.SelectedItems()
        if not selection:
            raise RuntimeError("Chọn một kết quả tìm kiếm.")
        selected_rows = selection.values() if isinstance(selection, dict) else selection
        row = next(iter(selected_rows), None)
        result = rows.get(row.Text[6]) if row is not None else None
        if result is None:
            raise RuntimeError("Chọn một kết quả tìm kiếm.")
        return result

    @handle_errors
    def choose_source():
        project, _ = get_project_index()
        hit, _ = selected()
        if project.GetUniqueId() == search_project_id and select_source(project, hit.media.key):
            message.Text = f"Đã chọn {hit.media.name} · Mốc nguồn {source_time(hit.start_ms)}."
        else:
            message.Text = "Media không còn trong project này. Hãy tìm lại."

    @handle_errors
    def mark():
        project, _ = get_project_index()
        hit, result = selected()
        if result is None or snapshot is None:
            raise RuntimeError("Chọn kết quả Trong timeline có vị trí đã xác minh.")
        timeline = project.GetCurrentTimeline()
        changed_source = Media.from_path(hit.media.key, hit.media.name, hit.media.path).revision != hit.media.revision
        if (project.GetUniqueId() != search_project_id or not timeline or changed_source
                or inspect_timeline(timeline) != snapshot):
            raise RuntimeError("Vị trí tìm kiếm có thể đã đổi. Hãy tìm lại trước khi đặt marker.")
        state = add_search_marker(timeline, snapshot, result, search_id)
        message.Text = {
            "added": "Đã đặt marker tạm trên thước timeline.",
            "exists": "Lượt tìm này đã đánh dấu frame đó.",
            "occupied": "Frame này đã có marker.",
            "stale": "Timeline đã đổi. Hãy tìm lại trước khi đặt marker.",
            "failed": "Resolve không tạo được marker tại frame này.",
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

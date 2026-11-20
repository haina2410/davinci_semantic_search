"""Transcript search window launched from Resolve's Workspace menu."""

from __future__ import annotations

from functools import wraps
from uuid import uuid4

from .index import Index, Media, project_db
from .resolve import read_timeline, scan_media, select_source
from .runtime import Settings
from .search import Hit, search
from .timeline import Snapshot, TimelineHit, add_search_marker, clear_search_markers, project_hits, timecode
from .timeline_audit import audit_timeline
from .worker import request_index, worker_active


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
            ui.HGroup({}, [
                ui.Button({"ID": "Index", "Text": "Lập chỉ mục project", "Weight": 0}),
                ui.Button({"ID": "Refresh", "Text": "Cập nhật tiến độ", "Weight": 0}),
                ui.Label({"ID": "IndexStatus", "Text": "", "Weight": 1}),
            ]),
            ui.HGroup({}, [
                ui.ComboBox({"ID": "Scope", "Weight": 0}),
                ui.LineEdit({"ID": "Query", "PlaceholderText": "Nhập lời thoại tiếng Việt hoặc tiếng Anh"}),
                ui.CheckBox({"ID": "Original", "Text": "Tìm thêm trong bản gốc", "Checked": False, "Weight": 0}),
                ui.Button({"ID": "Search", "Text": "Tìm", "Weight": 0}),
            ]),
            ui.Tree({"ID": "Results", "AlternatingRowColors": True, "SortingEnabled": False, "Weight": 1}),
            ui.HGroup({}, [
                ui.Label({"ID": "Message", "WordWrap": True, "Text": "", "Weight": 1}),
                ui.Button({"ID": "SelectSource", "Text": "Chọn trong Media Pool", "Weight": 0}),
                ui.Button({"ID": "Marker", "Text": "Đặt marker tạm", "Weight": 0}),
            ]),
        ])],
    )
    widgets = window.GetItems()
    scope = widgets["Scope"]
    scope.AddItem("Nguồn")
    scope.AddItem("Timeline")
    tree = widgets["Results"]
    tree.ColumnCount = 7
    header = tree.NewItem()
    for index, (label, width) in enumerate((
        ("Phạm vi", 125), ("Media nguồn", 185), ("Mốc nguồn", 110), ("Timeline", 145),
        ("Lời thoại", 298), ("Transcript", 128), ("ID", 0)
    )):
        header.Text[index] = label
        tree.ColumnWidth[index] = width
    tree.SetHeaderItem(header)

    manager = resolve.GetProjectManager()
    current_index: Index | None = None
    snapshot: Snapshot | None = None
    search_project_id = ""
    search_id = ""
    rows: dict[str, tuple[Hit, TimelineHit | None]] = {}
    message = widgets["Message"]

    def get_project_index():
        nonlocal current_index
        project = manager.GetCurrentProject()
        if not project:
            raise RuntimeError("Mở một project DaVinci Resolve trước.")
        path = project_db(settings.data_dir, project.GetUniqueId())
        if current_index is None or current_index.path != path:
            current_index = Index(path)
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
        running = worker_active(index)
        status = index.status()
        widgets["Index"].Enabled = not running
        widgets["Index"].Text = "Tiếp tục lập chỉ mục" if status.needs_attention else "Lập chỉ mục project"
        names = {"asr": "chờ nhận dạng", "polish": "chờ Codex", "done": "đã hiệu đính",
                 "silent": "không có lời thoại", "offline": "media ngoại tuyến"}
        counts = " · ".join(f"{count} {names[stage]}" for stage, count in status.phases.items())
        if running:
            activity = "Đang lập chỉ mục"
        elif status.requested:
            activity = "Lượt trước chưa hoàn tất"
        elif status.errors:
            activity = f"{len(status.errors)} media cần thử lại"
        else:
            activity = "Chỉ mục"
        widgets["IndexStatus"].Text = f"{activity} · {counts}" if counts else activity
        widgets["IndexStatus"].ToolTip = "\n".join(
            f"{name} — {detail}" for name, detail in status.errors[:10]
        )

    @handle_errors
    def refresh():
        _, index = get_project_index()
        update_progress(index)

    @handle_errors
    def start_index():
        project, index = get_project_index()
        if request_index(index, settings, lambda: scan_media(project)):
            message.Text = "Đã gửi lượt lập chỉ mục. Bấm Cập nhật tiến độ để xem trạng thái."
        else:
            message.Text = "Project này đã có lượt lập chỉ mục đang chạy."
        update_progress(index)

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
        if not selection or selection[0].Text[6] not in rows:
            raise RuntimeError("Chọn một kết quả tìm kiếm.")
        return rows[selection[0].Text[6]]

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
    window.On.Refresh.Clicked = refresh
    window.On.Query.ReturnPressed = find
    window.On.Search.Clicked = find
    window.On.SelectSource.Clicked = choose_source
    window.On.Marker.Clicked = mark
    window.On.TranscriptSearch.Close = lambda event: dispatch.ExitLoop()
    refresh()
    window.Show()
    try:
        dispatch.RunLoop()
    finally:
        window.Hide()

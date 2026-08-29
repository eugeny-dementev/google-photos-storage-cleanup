from gpsync.progress import (
    COLLAPSE_WORKERS,
    DownloadStatus,
    format_album_line,
    format_worker_cluster,
    vislen,
)


def test_worker_cluster_packs_one_letter_per_worker() -> None:
    text = format_worker_cluster(["d", "s"], n_workers=2, budget=20, color=False)
    assert text == "ds"


def test_worker_cluster_collapses_when_many_workers() -> None:
    letters = ["d"] * 6 + ["s"] * 4
    text = format_worker_cluster(letters, n_workers=COLLAPSE_WORKERS + 1, budget=40, color=False)
    assert text == "6d4s"
    packed = format_worker_cluster(["d", "s"], n_workers=2, budget=20, color=False)
    assert packed == "ds"


def test_worker_cluster_hides_when_no_budget() -> None:
    assert format_worker_cluster(["d", "s"], n_workers=2, budget=0, color=False) == ""
    assert format_worker_cluster(["d"] * 12, n_workers=12, budget=2, color=False) == ""


def test_worker_cluster_color_wraps_letters_not_width() -> None:
    text = format_worker_cluster(["d", "!"], n_workers=2, budget=20, color=True)
    assert "d" in text and "!" in text
    assert "\033[32m" in text
    assert "\033[31m" in text
    assert vislen(text) == 2


def test_album_line_right_aligns_letters_and_done() -> None:
    live = format_album_line(
        "Vacation 2024",
        letters=["d", "s"],
        done=False,
        width=72,
        color=False,
        n_workers=2,
    )
    assert live.startswith('Downloading album "Vacation 2024"')
    assert live.endswith("ds")
    assert vislen(live) == 72

    finished = format_album_line(
        "Vacation 2024",
        letters=["d"],
        done=True,
        width=72,
        color=False,
        n_workers=2,
    )
    assert finished.endswith("done")
    assert vislen(finished) == 72


def test_album_line_truncates_title_to_keep_done() -> None:
    title = "A very long album name that will not fit in a narrow terminal window"
    line = format_album_line(
        title, letters=["d"], done=True, width=40, color=False, n_workers=2
    )
    assert line.startswith("Downloading album \"")
    assert line.endswith("done")
    assert vislen(line) <= 40
    assert "..." in line


def test_album_line_drops_letters_before_breaking_layout() -> None:
    line = format_album_line(
        "Vacation 2024",
        letters=["d", "s"],
        done=False,
        width=26,
        color=False,
        n_workers=2,
    )
    assert "Downloading album" in line
    assert vislen(line) <= 28


def test_status_groups_letters_by_album_and_marks_done() -> None:
    status = DownloadStatus(n_workers=2)
    status.plan(
        {"a": 2, "b": 1},
        {"a": "Vacation 2024", "b": "Shared trip"},
    )
    status.set_worker(0, "a", "Vacation 2024", "d")
    status.set_worker(1, "a", "Vacation 2024", "s")
    assert status.letters_for("a") == ["d", "s"]
    status.set_worker(1, "b", "Shared trip", "d")
    assert status.letters_for("a") == ["d"]
    assert status.letters_for("b") == ["d"]
    assert status.complete_item("a") is False
    assert status.complete_item("a") is True
    lines = status.lines(width=80, color=False)
    assert lines[0].endswith("done")
    assert "Vacation 2024" in lines[0]
    assert lines[1].endswith("d")
    assert "Shared trip" in lines[1]

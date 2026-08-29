from gpsync.download import (
    DownloadOptions,
    Interrupted,
    _stop,
    _wait_download_slot,
    request_stop,
    stop_requested,
)


def test_wait_slot_raises_when_stopped() -> None:
    _stop.clear()
    request_stop()
    try:
        raised = False
        try:
            _wait_download_slot(DownloadOptions())
        except Interrupted:
            raised = True
        assert raised
        assert stop_requested()
    finally:
        _stop.clear()


def test_wait_slot_pause_aborts_quickly_when_stopped() -> None:
    _stop.clear()
    request_stop()
    try:
        raised = False
        try:
            _wait_download_slot(DownloadOptions(pause_min=30, pause_max=30))
        except Interrupted:
            raised = True
        assert raised
    finally:
        _stop.clear()

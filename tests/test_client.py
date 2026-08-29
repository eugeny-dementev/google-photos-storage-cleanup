from gpsync.client import PhotosApiError, _extract_wrb_payload
import json
import pytest


def test_extract_wrb_payload() -> None:
    inner = json.dumps([["AF1"]])
    body = ")]}'\n\n12\n" + json.dumps([["wrb.fr", "Z5xsfc", inner, None, None, None, "generic"]]) + "\n"
    assert _extract_wrb_payload(body) == [["AF1"]]


def test_extract_wrb_missing() -> None:
    with pytest.raises(PhotosApiError):
        _extract_wrb_payload("not an api response")

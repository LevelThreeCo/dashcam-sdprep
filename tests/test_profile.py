from __future__ import annotations

import copy
import json
from importlib import resources

import pytest

from dashcam_sdprep import profile
from dashcam_sdprep.errors import SdprepError


def _raw() -> dict:
    text = resources.files("dashcam_sdprep").joinpath("profiles", "vugera-vg-701v3.json").read_text()
    return json.loads(text)


def test_every_bundled_profile_loads():
    ids = profile.available()
    assert "vugera-vg-701v3" in ids
    for pid in ids:
        assert profile.load(pid).id == pid


def test_unknown_profile_is_refused():
    with pytest.raises(SdprepError, match="unknown profile"):
        profile.load("nope")


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["filesystem"].update(type="exFAT"), "not supported"),
    (lambda d: d["partition"].update(scheme="gpt"), "MBR"),
    (lambda d: d["filesystem"].update(cluster_table=[[10, 1], [5, 2]]), "ascending"),
    (lambda d: d["filesystem"].update(bytes_per_sector="512"), "must be int"),
    (lambda d: d["recordings"].update(pattern="^(?P<kind>REC).avi$"), "named groups"),
    (lambda d: d.pop("evidence"), "missing 'evidence'"),
])
def test_malformed_profiles_fail_loudly(mutate, message):
    data = copy.deepcopy(_raw())
    mutate(data)
    with pytest.raises(SdprepError, match=message):
        profile.parse(data)

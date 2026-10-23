"""Playback position without speakers: a fake play, stop and clock."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playback import COMPARE_GAP_SECONDS, Player  # noqa: E402

SR = 44100


class FakeClock:
    now = 100.0

    def __call__(self):
        return self.now


def make_player():
    played, stopped, clock = [], [], FakeClock()
    player = Player(play=lambda audio, rate: played.append(len(audio)), stop=lambda: stopped.append(1),
                    clock=clock, sample_rate=SR)
    return player, played, stopped, clock


def test_position_walks_take_gap_compare():
    player, played, stopped, clock = make_player()
    take, other = np.zeros(2 * SR), np.zeros(SR)
    start = player.play_take(take, start=0.5, then=other, owner="card")
    assert start == 0.5 and played == [int(1.5 * SR) + int(COMPARE_GAP_SECONDS * SR) + SR]
    finished = []
    player.on_finished(finished.append)
    clock.now += 1.0
    assert player.position() == {"playing": True, "piece": "take", "index": 0, "seconds": 1.5}
    clock.now += 0.75                                    # 1.5 s of take, then 0.25 s into the gap
    assert player.position()["piece"] == "gap" and player.position()["seconds"] is None
    clock.now += 0.5
    position = player.position()
    assert position["piece"] == "compare" and abs(position["seconds"] - 0.25) < 1e-9
    clock.now += 1.0                                     # past the end
    assert player.position() == {"playing": False, "piece": None, "seconds": None, "index": None}
    assert stopped == [1] and finished == ["card"] and player.owner is None


def test_start_stays_inside_the_take_and_another_owner_is_stopped():
    player, played, stopped, clock = make_player()
    finished = []
    player.on_finished(finished.append)
    assert player.play_take(np.zeros(SR), start=5.0, owner="a") == 1.0 - 0.05
    player.play_take(np.zeros(SR), start=0.2, owner="a")      # seeking: same owner, no stop
    assert stopped == [] and finished == []
    player.play_take(np.zeros(SR), owner="b")
    assert stopped == [1] and finished == ["a"] and player.owner == "b"
    ended = []
    player.on_finished(lambda: ended.append(True))              # a handler may ignore the owner
    player.stop()
    player.stop()                                              # stopping twice stops once
    assert stopped == [1, 1] and finished == ["a", "b"] and ended == [True]


def test_tracking_a_shifted_playback_plays_nothing():
    player, played, stopped, clock = make_player()
    player.track_take(np.zeros(2 * SR), start=0.5, owner="card")
    assert played == [] and player.playing
    clock.now += 1.0
    assert player.position()["seconds"] == 1.5
    player.sound(np.zeros(10))
    assert played == [10] and player.playing                   # a preview doesn't change the tracking

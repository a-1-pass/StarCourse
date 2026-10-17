# _*_ coding:utf-8 _*_
"""视频倍速 speed 配置的纯逻辑测试：

- resolve_speed：钳制 [1.0, 2.0]、非法值回退 1.0
- advance_play_time：倍率时间换算与目标时长钳制
- Video 实例默认 speed=1.0（不影响既有行为）
"""
from __future__ import annotations

import pytest

from classis.Media.Video import SPEED_DEFAULT, Video, advance_play_time, resolve_speed


def _cfg(speed):
    """构造仅含 speed 字段的最小配置结构。"""
    return {"FunConfig": {"deal-mission": {"speed": speed}}}


class TestResolveSpeed:
    def test_default_when_missing(self):
        assert resolve_speed({}) == 1.0
        assert resolve_speed({"FunConfig": {}}) == 1.0
        assert resolve_speed({"FunConfig": {"deal-mission": {}}}) == 1.0
        assert resolve_speed(None) in (1.0, SPEED_DEFAULT)  # 无配置时回退默认

    def test_normal_value_passthrough(self):
        assert resolve_speed(_cfg(1.0)) == 1.0
        assert resolve_speed(_cfg(1.5)) == 1.5
        assert resolve_speed(_cfg(2.0)) == 2.0
        assert resolve_speed(_cfg("1.8")) == pytest.approx(1.8)

    def test_clamp_upper_and_lower(self):
        assert resolve_speed(_cfg(2.5)) == 2.0
        assert resolve_speed(_cfg(100)) == 2.0
        assert resolve_speed(_cfg(0.5)) == 1.0
        assert resolve_speed(_cfg(0)) == 1.0
        assert resolve_speed(_cfg(-3)) == 1.0

    def test_invalid_values_fallback(self):
        assert resolve_speed(_cfg("abc")) == 1.0
        assert resolve_speed(_cfg(None)) == 1.0
        assert resolve_speed(_cfg([])) == 1.0
        assert resolve_speed(_cfg(float("nan"))) == 1.0

    def test_malformed_structure(self):
        assert resolve_speed({"FunConfig": None}) == 1.0
        assert resolve_speed({"FunConfig": {"deal-mission": None}}) == 1.0
        assert resolve_speed("not-a-dict") == 1.0


class TestAdvancePlayTime:
    def test_speed_1_matches_legacy_behavior(self):
        # speed=1.0 时与原 play_time + dt 完全一致
        assert advance_play_time(10.0, 0.5, 1.0, 100) == 10.5

    def test_speed_2_halves_real_wait(self):
        # 2 倍速：真实 0.5 秒推进 1 秒上报进度
        assert advance_play_time(10.0, 0.5, 2.0, 100) == 11.0

    def test_clamp_to_target_duration(self):
        assert advance_play_time(99.0, 5.0, 2.0, 100) == 100
        assert advance_play_time(99.0, 5.0, 1.0, 100) == 100

    def test_negative_dt_never_rewinds(self):
        assert advance_play_time(10.0, -1.0, 2.0, 100) == 10.0

    def test_speed_below_min_treated_as_1(self):
        assert advance_play_time(10.0, 0.5, 0.5, 100) == 10.5


class TestVideoDefaultSpeed:
    def test_default_speed_is_1(self):
        video = Video({"objectId": "oid"}, {"Cookie": "", "User-Agent": "UA"}, {})
        assert video.speed == 1.0

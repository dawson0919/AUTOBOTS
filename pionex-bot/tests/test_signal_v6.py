"""Unit tests for signal_v6.py"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

from signal_v6 import (
    calc_raw_signal, SignalState, replay_signal_state, sma,
    SIG_HOLD, SIG_MAIN_LONG, SIG_MAIN_SHORT, SIG_CORR_SHORT, SIG_BOUNCE_LONG,
)


def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0  # (3+4+5)/3
    assert sma([10, 20], 5) == 0.0  # not enough data


def test_main_long():
    # price > lb, price > gy (with dist), price > zf
    sig = calc_raw_signal(price=100, lb=90, gy=80, zf=95, dist_pct=2.0)
    assert sig == SIG_MAIN_LONG


def test_main_short():
    sig = calc_raw_signal(price=70, lb=90, gy=80, zf=75, dist_pct=2.0)
    assert sig == SIG_MAIN_SHORT


def test_corr_short():
    # is_bull (price > lb) but below_gy
    sig = calc_raw_signal(price=92, lb=90, gy=95, zf=93, dist_pct=2.0)
    assert sig == SIG_CORR_SHORT


def test_bounce_long():
    # is_bear but above_gy, bounce enabled
    sig = calc_raw_signal(price=88, lb=90, gy=80, zf=85, dist_pct=2.0, disable_bounce=False)
    assert sig == SIG_BOUNCE_LONG


def test_bounce_disabled():
    sig = calc_raw_signal(price=88, lb=90, gy=80, zf=85, dist_pct=2.0, disable_bounce=True)
    assert sig == SIG_HOLD  # bounce disabled


def test_dist_filter():
    # price > lb, price > gy but NOT far enough
    sig = calc_raw_signal(price=81, lb=70, gy=80, zf=79, dist_pct=5.0)
    assert sig == SIG_HOLD  # only 1.25% from gy, needs 5%


def test_signal_state_flip():
    ss = SignalState()
    changed, d = ss.update(SIG_MAIN_LONG)
    assert changed == True  # from 0 to LONG
    assert d == 1

    changed, d = ss.update(SIG_HOLD)
    assert changed == False  # HOLD doesn't flip

    changed, d = ss.update(SIG_MAIN_SHORT)
    assert changed == True  # LONG to SHORT
    assert d == -1


def test_signal_state_no_flip_same_dir():
    ss = SignalState(SIG_MAIN_LONG)
    changed, d = ss.update(SIG_BOUNCE_LONG)
    assert changed == False  # both are LONG direction


def test_replay():
    # Create price series that crosses above SMA
    closes = [50.0] * 20 + [51, 52, 53, 55, 58, 60, 62, 65, 68, 70]
    state = replay_signal_state(closes, lb_period=10, gy_period=5, zf_period=3, dist_pct=0)
    assert state.current_direction == 1  # should end up LONG


def test_replay_short():
    closes = [70.0] * 20 + [69, 68, 66, 64, 62, 60, 58, 55, 52, 50]
    state = replay_signal_state(closes, lb_period=10, gy_period=5, zf_period=3, dist_pct=0)
    assert state.current_direction == -1  # should end up SHORT


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {t.__name__} -- {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__} -- {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")

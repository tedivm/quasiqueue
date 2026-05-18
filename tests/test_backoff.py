import multiprocessing as mp

from quasiqueue import Builder, Settings


def make_builder(max_size=100, **kwargs):
    ctx = mp.get_context("fork")
    queue = ctx.Queue(max_size)
    settings = Settings(**kwargs)
    return Builder(queue, settings, lambda: iter([]))


def test_backoff_progression():
    b = make_builder()
    b.full_consecutive = 0

    for i in range(1, 8):
        b.full_consecutive = i
        expected = min(1.0 * (2 ** (i - 1)), 90.0)
        assert b.full_queue_sleep_time() == expected, (
            f"consecutive={i}, expected={expected}, got={b.full_queue_sleep_time()}"
        )


def test_backoff_reset_on_zero():
    b = make_builder()
    b.full_consecutive = 5
    b.full_consecutive = 0
    assert b.full_queue_sleep_time() == 1.0


def test_backoff_cap():
    b = make_builder()
    b.full_consecutive = 10
    assert b.full_queue_sleep_time() == 90.0

    b.full_consecutive = 20
    assert b.full_queue_sleep_time() == 90.0


def test_backoff_custom_min_max():
    settings = Settings(full_queue_sleep_min=0.5, full_queue_sleep_max=10.0)
    ctx = mp.get_context("fork")
    queue = ctx.Queue(100)
    b = Builder(queue, settings, lambda: iter([]))

    b.full_consecutive = 1
    assert b.full_queue_sleep_time() == 0.5

    b.full_consecutive = 2
    assert b.full_queue_sleep_time() == 1.0

    b.full_consecutive = 3
    assert b.full_queue_sleep_time() == 2.0

    b.full_consecutive = 5
    assert b.full_queue_sleep_time() == 8.0

    b.full_consecutive = 10
    assert b.full_queue_sleep_time() == 10.0

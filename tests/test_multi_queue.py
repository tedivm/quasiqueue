import asyncio
import json
import multiprocessing as mp
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from quasiqueue import Builder
from quasiqueue.runner import QueueRunner
from quasiqueue.settings import Settings


class QuickTestSettings(Settings):
    graceful_shutdown_timeout: float = 0.2
    empty_queue_sleep_time: float = 0.05
    save_dir: str


class StopTestException(Exception):
    pass


# 4.1 Test: multi-queue concurrent execution
def test_multi_queue_concurrent():
    """Launch two QueueRunner instances via run_queues() with distinct writers/readers.
    Verify items flow to correct readers without cross-contamination.
    """
    with tempfile.TemporaryDirectory() as d:
        dir_a = Path(d) / "a"
        dir_b = Path(d) / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        script = f"""
import asyncio
from quasiqueue.runner import QueueRunner, run_queues
from quasiqueue.settings import Settings

class TestSettings(Settings):
    graceful_shutdown_timeout: float = 0.5
    empty_queue_sleep_time: float = 0.05
    num_processes: int = 2
    save_dir: str = ""

import json
from pathlib import Path

settings_a = TestSettings(save_dir=r"{dir_a}")
settings_b = TestSettings(save_dir=r"{dir_b}")

async def writer_a(desired, settings):
    for i in range(10):
        yield f"a_{{i}}"
    await asyncio.sleep(1)
    raise StopIteration("done")

async def writer_b(desired, settings):
    for i in range(10):
        yield f"b_{{i}}"
    await asyncio.sleep(1)
    raise StopIteration("done")

async def reader_a(item, settings):
    import json
    from pathlib import Path
    with open(Path(settings["save_dir"]) / f"{{item}}.output", "w") as f:
        json.dump({{"item": item, "queue": "a"}}, f)

async def reader_b(item, settings):
    import json
    from pathlib import Path
    with open(Path(settings["save_dir"]) / f"{{item}}.output", "w") as f:
        json.dump({{"item": item, "queue": "b"}}, f)

runner_a = QueueRunner(name="queue_a", reader=reader_a, writer=writer_a, settings=settings_a)
runner_b = QueueRunner(name="queue_b", reader=reader_b, writer=writer_b, settings=settings_b)

try:
    run_queues(runner_a, runner_b)
except:
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        files_a = list(dir_a.glob("*.output"))
        files_b = list(dir_b.glob("*.output"))

        assert len(files_a) > 0, f"Queue A should have processed items; stderr: {result.stderr}"
        assert len(files_b) > 0, f"Queue B should have processed items; stderr: {result.stderr}"

        # Verify no cross-contamination
        for f in files_a:
            data = json.load(open(f))
            assert data["item"].startswith("a_"), f"Queue A got wrong item: {data['item']}"
            assert data["queue"] == "a"

        for f in files_b:
            data = json.load(open(f))
            assert data["item"].startswith("b_"), f"Queue B got wrong item: {data['item']}"
            assert data["queue"] == "b"


# 4.2 Test: shared shutdown event
@pytest.mark.asyncio
async def test_shared_shutdown_event():
    """Verify both queues stop when shutdown_event is set."""
    with tempfile.TemporaryDirectory() as d:
        dir_a = Path(d) / "a"
        dir_b = Path(d) / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        settings_a = QuickTestSettings(save_dir=str(dir_a), num_processes=1)
        settings_b = QuickTestSettings(save_dir=str(dir_b), num_processes=1)

        async def writer_a(desired: int, settings: dict):
            for i in range(5):
                yield f"a_{i}"
            await asyncio.sleep(10)

        async def writer_b(desired: int, settings: dict):
            for i in range(5):
                yield f"b_{i}"
            await asyncio.sleep(10)

        async def reader_a(item: str | int, settings: dict):
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item}, f)

        async def reader_b(item: str | int, settings: dict):
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item}, f)

        runner_a = QueueRunner(
            name="shutdown_a",
            reader=reader_a,
            writer=writer_a,
            settings=settings_a,
        )
        runner_b = QueueRunner(
            name="shutdown_b",
            reader=reader_b,
            writer=writer_b,
            settings=settings_b,
        )

        ctx = mp.get_context("fork")
        shutdown_event = ctx.Event()
        runner_a.setup_signals(shutdown_event)

        async def _run():
            await asyncio.gather(
                runner_a._run_loop(shutdown_event),
                runner_b._run_loop(shutdown_event),
            )

        async def _trigger_shutdown():
            await asyncio.sleep(1)
            shutdown_event.set()

        await asyncio.gather(_run(), _trigger_shutdown())

        files_a = list(dir_a.glob("*.output"))
        files_b = list(dir_b.glob("*.output"))
        assert len(files_a) > 0, "Queue A should have processed some items"
        assert len(files_b) > 0, "Queue B should have processed some items"


# 4.3 Test: writer exhaustion detection
@pytest.mark.asyncio
async def test_writer_exhaustion():
    """Writer yields fixed items then returns None repeatedly.
    Verify Builder.exhausted becomes True after threshold.
    """
    ctx = mp.get_context("fork")
    queue = ctx.Queue(100)
    settings = Settings(max_queue_size=100, empty_queue_sleep_time=3)

    yield_count = 0

    async def exhausting_writer(desired: int):
        nonlocal yield_count
        if yield_count == 0:
            yield_count = 1
            for i in range(5):
                yield i
        else:
            yield None

    builder = Builder(queue, settings, exhausting_writer)

    result = await builder.populate()
    assert result is True
    assert builder.exhausted is False

    for _ in range(5):
        await builder.populate()

    assert builder.exhausted is True, "Builder should be exhausted after threshold"


# 4.3b Test: intermittent writer does not trigger exhaustion
@pytest.mark.asyncio
async def test_intermittent_writer_not_exhausted():
    """Writer yields, then None, then yields again. Should not trigger exhaustion."""
    ctx = mp.get_context("fork")
    queue = ctx.Queue(100)
    settings = Settings(max_queue_size=100, empty_queue_sleep_time=3)

    call_count = 0

    async def intermittent_writer(desired: int):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 1:
            yield call_count
        else:
            yield None

    builder = Builder(queue, settings, intermittent_writer)

    for _ in range(6):
        await builder.populate()

    assert builder.exhausted is False, "Intermittent writer should not trigger exhaustion"


# 4.4 Test: Builder.close() sentinel delivery
@pytest.mark.asyncio
async def test_builder_close_sentinel():
    """Call close() on a builder, verify 'close' sentinels in queue."""
    ctx = mp.get_context("fork")
    queue = ctx.Queue(100)
    settings = Settings(max_queue_size=100, lookup_block_size=10, queue_interaction_timeout=0.01)

    async def dummy_writer():
        yield 1

    builder = Builder(queue, settings, dummy_writer)

    await builder.populate()
    result = builder.close()
    assert result is True
    assert builder.closed is True

    # Allow feeder thread time to flush sentinels to the pipe
    await asyncio.sleep(0.2)

    items = []
    import queue as qmod

    while True:
        try:
            item = queue.get(False)
            items.append(item)
        except qmod.Empty:
            break

    assert "close" in items, f"Queue should contain 'close' sentinels after builder.close(), got: {items}"


# 4.4b Test: Builder.close() with Full exception
@pytest.mark.asyncio
async def test_builder_close_full_queue():
    """Queue at capacity — close() should not crash."""
    ctx = mp.get_context("fork")
    queue = ctx.Queue(3)
    settings = Settings(max_queue_size=3, lookup_block_size=10, queue_interaction_timeout=0.01)

    async def dummy_writer():
        yield 1

    builder = Builder(queue, settings, dummy_writer)

    queue.put("x")
    queue.put("y")
    queue.put("z")

    result = builder.close()
    assert result is True
    assert builder.closed is True


# 4.5 Test: _run_loop() isolation
@pytest.mark.asyncio
async def test_run_loop_isolation():
    """Call _run_loop() directly with manual shutdown_event."""
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(save_dir=d, num_processes=1)

        async def writer(desired: int, settings: dict):
            for i in range(3):
                yield i
            await asyncio.sleep(10)

        async def reader(item: str | int, settings: dict):
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item}, f)

        runner = QueueRunner(
            name="isolation_test",
            reader=reader,
            writer=writer,
            settings=settings,
        )

        ctx = mp.get_context("fork")
        shutdown_event = ctx.Event()

        async def _trigger():
            await asyncio.sleep(1)
            shutdown_event.set()

        await asyncio.gather(runner._run_loop(shutdown_event), _trigger())

        files = list(Path(d).glob("*.output"))
        assert len(files) > 0, "_run_loop should have processed items before shutdown"


# 4.6 Test: run_queues() with a single runner
def test_run_queues_single_runner():
    """Verify run_queues() with one runner behaves like asyncio.run(runner.main())."""
    with tempfile.TemporaryDirectory() as d:
        script = f"""
import asyncio
from quasiqueue.runner import QueueRunner, run_queues
from quasiqueue.settings import Settings

class TestSettings(Settings):
    graceful_shutdown_timeout: float = 0.3
    empty_queue_sleep_time: float = 0.05
    num_processes: int = 2
    save_dir: str = ""

import json
from pathlib import Path

settings = TestSettings(save_dir=r"{d}")

async def writer(desired, settings):
    for i in range(20):
        yield i
    await asyncio.sleep(0.5)
    raise StopIteration("done")

async def reader(item, settings):
    import json
    from pathlib import Path
    with open(Path(settings["save_dir"]) / f"{{item}}.output", "w") as f:
        json.dump({{"item": item}}, f)

runner = QueueRunner(name="single_test", reader=reader, writer=writer, settings=settings)

try:
    run_queues(runner)
except:
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )

        files = list(Path(d).glob("*.output"))
        assert len(files) > 0, f"Single runner via run_queues should process items; stderr: {result.stderr}"

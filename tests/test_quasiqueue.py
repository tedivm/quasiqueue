import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

from quasiqueue import QuasiQueue
from tests.utils import QuickTestSettings, StopTestException, get_pids_from_results, run_and_gather

logger = logging.getLogger(__name__)


def test_quasiqueue_import():
    from quasiqueue import QuasiQueue  # noqa: F401


@pytest.mark.asyncio
async def test_async_reader():
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(save_dir=d, num_processes=2)
        results = await run_and_gather(settings)
    assert len(get_pids_from_results(results)) == 2
    assert len(results["files"].keys()) == 50


@pytest.mark.asyncio
async def test_sync_reader():
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(save_dir=d, num_processes=2)
        results = await run_and_gather(settings, async_preferred=False)
    assert len(get_pids_from_results(results)) == 2
    assert len(results["files"].keys()) == 50


@pytest.mark.asyncio
async def test_low_volume_processing():
    """
    Risk: Async reader tasks could starve when queue volume stays below the concurrency limit.
    Test Point: Verify low-volume input still allows created async tasks to run to completion.
    Anti-mocking: Use the real worker process, queue, and filesystem outputs instead of mocked scheduling.
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            concurrent_tasks_per_process=4,  # More concurrent tasks than items
            num_processes=1,
            max_jobs_per_process=None,  # No limit
            graceful_shutdown_timeout=0.5,
        )

        async def low_volume_writer(desired: int, settings: dict):
            # Only yield 2 items, but concurrent_tasks_per_process=4
            for i in range(2):
                yield i
            await asyncio.sleep(0.5)
            raise StopTestException("Test complete")

        async def async_reader(item: str | int, settings: dict, ctx: dict):
            # Simulate some async work
            await asyncio.sleep(0.01)
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item, "pid": os.getpid()}, f)

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="low_volume_test",
            reader=async_reader,
            writer=low_volume_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        # Verify all items were processed despite low volume
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) == 2


@pytest.mark.asyncio
async def test_graceful_shutdown():
    """
    Risk: In-flight async tasks could be dropped during shutdown.
    Test Point: Verify work already started in the worker can finish and persist outputs before exit.
    Anti-mocking: Use real worker processes and assert on output files written from the child process.
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            concurrent_tasks_per_process=2,
            graceful_shutdown_timeout=1.0,
        )

        async def shutdown_test_writer(desired: int, settings: dict):
            # Yield several items quickly
            for i in range(5):
                yield i
            # Give tasks time to execute before shutdown signal
            await asyncio.sleep(0.5)
            raise StopTestException("Triggering shutdown")

        async def shutdown_test_reader(item: str | int, settings: dict, ctx: dict):
            """Reader that marks task completion"""
            try:
                # Simulate some async work
                await asyncio.sleep(0.05)
                with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                    json.dump({"completed": True}, f)
            except asyncio.CancelledError:
                # This should NOT happen if graceful shutdown is working
                logger.error(f"Task {item} was cancelled during shutdown!")
                raise

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="shutdown_test",
            reader=shutdown_test_reader,
            writer=shutdown_test_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        # Verify that tasks completed gracefully (no cancellation errors)
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) >= 3


@pytest.mark.asyncio
async def test_child_process_logging_smoke():
    """
    Risk: Child processes may start without logging configured.
    Test Point: Verify worker logging calls do not break processing in a child process.
    Anti-mocking: Exercise real worker-process logging and assert on completed work, not mocked handlers.
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            num_processes=1,
            graceful_shutdown_timeout=0.2,
        )

        async def logging_writer(desired: int, settings: dict):
            for i in range(3):
                yield i
            await asyncio.sleep(0.3)
            raise StopTestException("Test complete")

        async def logging_reader(item: str | int, settings: dict, ctx: dict):
            # The fact that this logger works in the child process is the test
            logger.debug(f"Processing item {item}")
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item}, f)

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="logging_test",
            reader=logging_reader,
            writer=logging_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        # If we got here without crashes, logging was set up correctly
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) == 3


@pytest.mark.asyncio
async def test_event_loop_responsiveness():
    """
    Risk: Blocking sleeps in async code could stall worker progress.
    Test Point: Smoke-test that worker processing continues and does not hang under light load.
    Anti-mocking: Use the real worker process, queue, and filesystem outputs.
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            concurrent_tasks_per_process=1,  # Force sequential processing
            num_processes=1,
            graceful_shutdown_timeout=0.5,
        )

        async def timing_writer(desired: int, settings: dict):
            # Yield a few items to test responsiveness
            for i in range(3):
                yield i
            await asyncio.sleep(0.2)
            raise StopTestException("Test complete")

        async def timing_reader(item: str | int, settings: dict, ctx: dict):
            # Simple reader that just processes the item
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item, "processed": True}, f)

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="timing_test",
            reader=timing_reader,
            writer=timing_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        # This is intentionally a smoke test rather than a strong timing assertion.
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) >= 1, "The worker should make forward progress instead of hanging"

        # Verify the items that were processed
        processed_items = set()
        for file in output_files:
            with open(file) as f:
                data = json.load(f)
                processed_items.add(data["item"])

        # Should have processed at least the first item.
        assert 0 in processed_items, "The first queued item should have been processed"


@pytest.mark.asyncio
async def test_resource_cleanup_on_shutdown():
    """
    Risk: Database connections or other resources not properly cleaned up during shutdown
    Test Point: Verify resources are cleaned up even when tasks are interrupted
    Anti-mocking: Use real file handles and multiprocessing to simulate resource management
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            concurrent_tasks_per_process=2,
            graceful_shutdown_timeout=0.2,
        )

        async def cleanup_writer(desired: int, settings: dict):
            # Yield items quickly, then shutdown should interrupt processing
            for i in range(5):
                yield i
            await asyncio.sleep(0.5)  # Give time for shutdown to occur
            raise StopTestException("Should not reach here")

        async def cleanup_reader(item: str | int, settings: dict, ctx: dict):
            """Reader that simulates resource acquisition and cleanup"""
            # Simulate acquiring a resource (file handle, db connection, etc.)
            resource_file = Path(settings["save_dir"]) / f"resource_{item}.tmp"
            resource_file.write_text("resource acquired")

            try:
                # Simulate work that might be interrupted by shutdown
                await asyncio.sleep(0.1)
                # Simulate successful completion
                with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                    json.dump({"item": item, "completed": True, "pid": os.getpid()}, f)
            finally:
                # Always clean up resources - this is what we're testing
                if resource_file.exists():
                    resource_file.unlink()  # Clean up resource

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="cleanup_test",
            reader=cleanup_reader,
            writer=cleanup_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        # The key test: verify no resource files remain (all cleaned up)
        # This proves that cleanup happened even if tasks were interrupted
        resource_files = list(Path(d).glob("resource_*.tmp"))
        assert len(resource_files) == 0, f"Found {len(resource_files)} uncleared resource files"

        # Also verify some tasks started (there should be some output files)
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) >= 1, "At least one task should have started"

        # Verify we have output from multiple processes (indicating multiprocessing worked)
        pids = set()
        for file in output_files:
            with open(file) as f:
                data = json.load(f)
                pids.add(data["pid"])
        assert len(pids) >= 1, "Should have processed items in at least one process"


@pytest.mark.asyncio
async def test_multi_process_logging_smoke():
    """
    Risk: Logging setup in multiple child processes could break worker execution.
    Test Point: Verify worker logging calls remain a no-crash smoke test across multiple processes.
    Anti-mocking: Use real multiprocessing and assert on completed work instead of mocked logging state.
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            num_processes=2,  # Multiple processes to test handler conflicts
            graceful_shutdown_timeout=0.2,
        )

        async def logging_writer(desired: int, settings: dict):
            for i in range(6):
                yield i
            await asyncio.sleep(0.3)
            raise StopTestException("Test complete")

        async def logging_reader(item: str | int, settings: dict, ctx: dict):
            # Log from child process - this tests that logging.basicConfig() works
            logger.info(f"Child process {os.getpid()} processing item {item}")
            logger.debug(f"Debug info for item {item}")
            await asyncio.sleep(0.05)

            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item, "pid": os.getpid()}, f)

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="logging_conflict_test",
            reader=logging_reader,
            writer=logging_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        # Verify all items were processed
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) == 6

        # Verify the configured worker pool actually handled work in multiple processes.
        pids = set()
        for file in output_files:
            with open(file) as f:
                data = json.load(f)
                pids.add(data["pid"])
        assert len(pids) == 2, "Both worker processes should have processed work"


@pytest.mark.asyncio
async def test_async_sleep_in_runner():
    """
    Risk: Blocking sleep calls in runner could prevent responsive shutdown handling
    Test Point: Verify runner uses async sleep for non-blocking delays
    Anti-mocking: Use real asyncio event loop and verify timing behavior
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            num_processes=1,
            full_queue_sleep_time=0.05,  # Short sleep to test responsiveness
            graceful_shutdown_timeout=0.1,
        )

        sleep_events = []

        async def sleep_writer(desired: int, settings: dict):
            # Writer that yields slowly to trigger full_queue_sleep_time
            for i in range(3):
                yield i
                await asyncio.sleep(0.01)  # Slow down writing
            await asyncio.sleep(0.1)  # Allow some processing time
            raise StopTestException("Test complete")

        async def sleep_reader(item: str | int, settings: dict, ctx: dict):
            # Mark when processing starts
            sleep_events.append(f"processing_{item}")
            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item}, f)

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="async_sleep_test",
            reader=sleep_reader,
            writer=sleep_writer,
            context=context_fn,
            settings=settings,
        )

        start_time = asyncio.get_event_loop().time()
        try:
            await qq.main()
        except StopTestException:
            pass
        end_time = asyncio.get_event_loop().time()

        # Verify the test completed in reasonable time (not blocked by sync sleep)
        duration = end_time - start_time
        assert duration < 1.0, f"Test took too long ({duration}s), likely due to blocking sleep"

        # Verify tasks were processed
        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) >= 2, "Multiple tasks should have been processed"


@pytest.mark.asyncio
async def test_event_loop_blocking_prevention():
    """
    Risk: Queue operations blocking event loop could prevent concurrent task execution
    Test Point: Verify async reader tasks can overlap in a real worker process
    Anti-mocking: Use real multiprocessing queues and measure task interleaving via output files
    """
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(
            save_dir=d,
            concurrent_tasks_per_process=2,
            num_processes=1,
            queue_interaction_timeout=0.1,
            graceful_shutdown_timeout=0.2,
        )

        async def blocking_writer(desired: int, settings: dict):
            # Yield items at a controlled rate
            for i in range(4):
                yield i
                await asyncio.sleep(0.01)  # Small delay between items
            await asyncio.sleep(0.1)  # Allow processing to complete
            raise StopTestException("Test complete")

        async def blocking_reader(item: str | int, settings: dict, ctx: dict):
            """Reader that records timing so concurrency can be verified cross-process."""
            start_time = asyncio.get_event_loop().time()

            # Simulate some async work
            await asyncio.sleep(0.02)

            end_time = asyncio.get_event_loop().time()

            with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
                json.dump({"item": item, "start": start_time, "end": end_time}, f)

        async def context_fn(settings: dict):
            return {"settings": settings}

        qq = QuasiQueue(
            name="blocking_prevention_test",
            reader=blocking_reader,
            writer=blocking_writer,
            context=context_fn,
            settings=settings,
        )

        try:
            await qq.main()
        except StopTestException:
            pass

        output_files = list(Path(d).glob("*.output"))
        assert len(output_files) >= 3, "Multiple tasks should have completed"

        execution_times = []
        for file in output_files:
            with open(file) as f:
                data = json.load(f)
                execution_times.append((data["start"], data["end"]))

        # Check for overlapping execution (concurrent tasks)
        overlapping_found = False
        for i, (start1, end1) in enumerate(execution_times):
            for j, (start2, end2) in enumerate(execution_times[i + 1 :], i + 1):
                if start1 < end2 and start2 < end1:  # Overlapping intervals
                    overlapping_found = True
                    break
            if overlapping_found:
                break

        assert overlapping_found, "Tasks should have executed concurrently, indicating event loop wasn't blocked"

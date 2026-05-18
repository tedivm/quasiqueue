import asyncio
import logging
import multiprocessing as mp
import signal
import time
from typing import Any, Callable, Dict, List

import psutil

from .builder import Builder
from .reader import reader_process
from .settings import Settings, get_named_settings

logger = logging.getLogger(__name__)


class QueueRunner(object):
    """Coordinate queue creation, worker supervision, and shutdown behavior."""

    def __init__(
        self,
        name: str,
        reader: Callable[[str | int], None],
        writer: Callable[[], int | str | None],
        context: Callable[[], Dict[str, Any]] | None = None,
        settings: Settings | None = None,
    ) -> None:
        """The QueueRunner orchestrates the various components of the queue systems.

        Args:
            name (str): The name of the queue, used for logging and custom environment variable settings.
            reader (Callable[[str  |  int], None]): A function that reads items off of the queue for processing.
            writer (Callable[[], int  |  str  |  None]): The function responsible for adding new items to the queue.
            context (Callable[[], Dict[str, Any]] | None): A function used to provide context to the Reader function when it is called. This is useful for reusing database connections or http connection pooling. The return value is a dict with any arbitrary keys defined. Defaults to None.
            settings (Settings | None, optional): A custom already initialized Settings object. Defaults to None.
        """
        self.name = name
        self.settings = settings if settings else get_named_settings(name)
        self.reader = reader
        self.writer = writer
        self.context = context
        self.worker_launches = 0

    def setup_signals(self, shutdown_event: mp.synchronize.Event) -> None:
        """Register signal handlers on the given event.

        Safe to call multiple times — each call attaches the same handler
        logic to the same event, so multiple QueueRunners can share a
        single shutdown_event for coordinated teardown.

        Args:
            shutdown_event: A multiprocessing Event that will be set when
                SIGINT or SIGTERM is received.
        """

        def shutdown(a=None, b=None):
            if a is not None:
                logger.debug(f"[{self.name}] Signal {a} caught.")

            shutdown_event.set()

            if a == 15 or a is None:
                logger.debug("Gracefully shutting down child processes.")
                shutdown_start = time.time()
                while len(psutil.Process().children()) > 0:
                    if time.time() > (shutdown_start + self.settings.graceful_shutdown_timeout):
                        break
                    time.sleep(0.05)

            remaining_processes = psutil.Process().children()
            if len(remaining_processes) > 0:
                logger.debug("Terminating remaining child processes.")
                for process in remaining_processes:
                    process.terminate()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

    async def _run_loop(self, shutdown_event: mp.synchronize.Event) -> None:
        """Per-queue async loop: spawn workers, populate queue, prune dead processes.

        Args:
            shutdown_event: Event that signals the loop to exit.
        """
        ctx = mp.get_context("fork")
        import_queue: mp.Queue = ctx.Queue(self.settings.max_queue_size)
        queue_builder = Builder(import_queue, self.settings, self.writer)

        try:
            processes: List[mp.process.BaseProcess] = []
            while not shutdown_event.is_set():
                processes = [x for x in processes if x.is_alive()]

                new_processes = 0
                while len(processes) < self.settings.num_processes:
                    process = self.launch_process(import_queue, shutdown_event)
                    processes.append(process)
                    process.start()
                    new_processes += 1

                if new_processes:
                    await asyncio.sleep(0.1)

                if not await queue_builder.populate():
                    logger.debug(f"[{self.name}] Queue unable to populate: sleeping scheduler.")
                    await asyncio.sleep(queue_builder.full_queue_sleep_time())
                else:
                    await asyncio.sleep(0.05)
        finally:
            logger.warning(f"[{self.name}] Shutting down all processes.")
            import_queue.close()
            import_queue.join_thread()
            logger.warning(f"[{self.name}] All processes shut down.")

    async def main(self) -> None:
        """Run the parent process that supervises workers and queue population.

        Backward-compatible entry point — sets up signals and runs the loop.
        """
        ctx = mp.get_context("fork")
        shutdown_event = ctx.Event()
        self.setup_signals(shutdown_event)

        try:
            await self._run_loop(shutdown_event)
        finally:
            shutdown_event.set()

    def launch_process(self, import_queue, shutdown_event) -> mp.process.BaseProcess:
        """Create one worker process with the queue contract it will consume."""
        ctx = mp.get_context("fork")
        process = ctx.Process(
            target=reader_process,
            args=(
                import_queue,
                shutdown_event,
                self.reader,
                self.context,
                self.settings.model_dump(),
            ),
        )
        process.name = f"worker_{self.worker_launches:03d}"
        self.worker_launches += 1
        logger.debug(f"Launching worker {process.name}")
        process.daemon = True
        return process


def run_queues(*runners: QueueRunner) -> None:
    """Run multiple QueueRunner instances in a single event loop.

    All runners share a single shutdown event and signal handler. When
    SIGINT or SIGTERM is received, every queue loop exits together.

    Args:
        *runners: Two or more QueueRunner instances to run concurrently.
    """
    ctx = mp.get_context("fork")
    shutdown_event = ctx.Event()

    runners[0].setup_signals(shutdown_event)

    async def _run_all():
        try:
            await asyncio.gather(*[runner._run_loop(shutdown_event) for runner in runners])
        finally:
            shutdown_event.set()

    asyncio.run(_run_all())

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

    async def main(self) -> None:
        """Run the parent process that supervises workers and queue population."""
        # Use fork explicitly so that worker callables (including locally-defined
        # functions) do not need to be picklable. Python 3.14 changed the default
        # start method on Linux to forkserver, which requires pickling all process
        # arguments; fork inherits the parent's address space and avoids that.
        _ctx = mp.get_context("fork")
        import_queue: mp.Queue = _ctx.Queue(self.settings.max_queue_size)
        queue_builder = Builder(import_queue, self.settings, self.writer)
        shutdown_event = _ctx.Event()

        def shutdown(a=None, b=None):
            # Inline function to implicitly pass through shutdown_event.
            if a is not None:
                logger.debug(f"Signal {a} caught.")

            # Send shutdown signal to all processes.
            shutdown_event.set()

            # Graceful shutdown- wait for children to shut down.
            if a == 15 or a is None:
                logger.debug("Gracefully shutting down child processes.")
                logger.debug(self.settings.graceful_shutdown_timeout)
                shutdown_start = time.time()
                while len(psutil.Process().children()) > 0:
                    if time.time() > (shutdown_start + self.settings.graceful_shutdown_timeout):
                        break
                    time.sleep(0.05)

            # Kill any remaining processes directly, not counting on variables.
            remaining_processes = psutil.Process().children()
            if len(remaining_processes) > 0:
                logger.debug("Terminating remaining child processes.")
                for process in remaining_processes:
                    process.terminate()

        # Set shutdown function as signal handler for SIGINT and SIGTERM.
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        try:
            # Now start actual script.
            processes: List[mp.Process] = []
            while not shutdown_event.is_set():
                # Prune dead processes
                processes = [x for x in processes if x.is_alive()]

                # Bring process list up to size
                new_processes = 0
                while len(processes) < self.settings.num_processes:
                    process = self.launch_process(import_queue, shutdown_event)
                    processes.append(process)
                    process.start()
                    new_processes += 1

                if new_processes:
                    # Give newly-started workers time to initialize and begin
                    # blocking on queue.get() before we fill the queue, so that
                    # items are distributed fairly across all workers rather than
                    # being consumed entirely by whichever process starts first.
                    await asyncio.sleep(0.1)

                # Populate Queue
                if not await queue_builder.populate():
                    logger.debug("Queue unable to populate: sleeping scheduler.")
                    await asyncio.sleep(self.settings.full_queue_sleep_time)
                else:
                    # Small sleep between populate attempts to prevent CPU/database pegging.
                    await asyncio.sleep(0.05)
        finally:
            logger.warning("Shutting down all processes.")
            shutdown()
            # Explicitly close the queue now that the parent owns its
            # lifecycle directly instead of delegating it to a Manager.
            import_queue.close()
            import_queue.join_thread()
            logger.warning("All processes shut down.")

    def launch_process(self, import_queue, shutdown_event) -> mp.Process:
        """Create one worker process with the queue contract it will consume."""
        _ctx = mp.get_context("fork")
        process = _ctx.Process(
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

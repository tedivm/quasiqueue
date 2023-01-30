import asyncio
import inspect
import logging
import multiprocessing as mp
import time
from multiprocessing.synchronize import Event
from queue import Empty
from typing import Any, Callable, Dict


def reader_process(
    queue: mp.Queue,
    shutdown_event: Event,
    reader: Callable[[str | int], None],
    context: Callable[[], Dict[str, Any]] | None,
    settings: Dict[str, Any],
) -> None:
    asyncio.run(reader_runner(queue, shutdown_event, reader, context, settings))


async def reader_runner(
    queue: mp.Queue,
    shutdown_event: Event,
    reader: Callable[[str | int], None],
    context: Callable[[], Dict[str, Any]] | None,
    settings: dict,
) -> None:
    PROCESS_NAME = mp.current_process().name
    jobs_run = 0

    parent_process = mp.parent_process()
    if not parent_process:
        raise ValueError("Function should be called as a child process.")

    ctx = None
    if context:
        ctx = context()

    while not shutdown_event.is_set() and parent_process.is_alive():
        try:
            id = queue.get(True, settings["queue_interaction_timeout"])
            if id == "close":
                break

            params = [id]

            if ctx:
                params.append(ctx)

            if inspect.iscoroutinefunction(reader):
                await reader(*params)
            else:
                reader(*params)

            if settings.get("max_jobs_per_process", None):
                jobs_run += 1
                if jobs_run >= settings["max_jobs_per_process"]:
                    logging.info(f"{PROCESS_NAME} has reached max_jobs_per_process, exiting.")
                    return

        except Empty:
            logging.debug(f"{PROCESS_NAME} has no jobs to process, sleeping.")
            time.sleep(settings["empty_queue_sleep_time"])
            continue

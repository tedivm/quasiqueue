import asyncio
import inspect
import logging
import multiprocessing as mp
import time
from multiprocessing.synchronize import Event
from queue import Empty
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


def reader_process(
    queue: mp.Queue,
    shutdown_event: Event,
    reader: Callable[[str | int], None],
    context: Callable[[], Dict[str, Any]] | None,
    settings: Dict[str, Any],
) -> None:
    asyncio.run(reader_runner(queue, shutdown_event, reader, context, settings))


def _prune_tasks(tasks: List[asyncio.Task]) -> List[asyncio.Task]:
    return [task for task in tasks if not task.done()]


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
        context_args = inspect.getfullargspec(context).args
        context_kw_args = {}
        if "settings" in context_args:
            context_kw_args["settings"] = settings

        if inspect.iscoroutinefunction(context):
            ctx = await context(**context_kw_args)
        else:
            ctx = context(**context_kw_args)

    running_tasks: List[asyncio.Task] = []
    reader_args = inspect.getfullargspec(reader).args

    while not shutdown_event.is_set() and parent_process.is_alive():
        try:
            item = queue.get(True, settings["queue_interaction_timeout"])
            if item == "close":
                break

            reader_kw_args = {"item": item}

            if ctx:
                if "ctx" in reader_args:
                    reader_kw_args["ctx"] = ctx

            if "settings" in reader_args:
                reader_kw_args["settings"] = settings

            if inspect.iscoroutinefunction(reader):
                running_tasks = _prune_tasks(running_tasks)
                while len(running_tasks) >= settings["concurrent_tasks_per_process"]:
                    await asyncio.sleep(0.01)
                    running_tasks = _prune_tasks(running_tasks)
                running_tasks.append(asyncio.create_task(reader(**reader_kw_args)))  # type: ignore
            else:
                reader(**reader_kw_args)  # type: ignore

            jobs_run += 1
            if settings.get("max_jobs_per_process", None):
                if jobs_run >= settings["max_jobs_per_process"]:
                    logger.info(f"{PROCESS_NAME} has reached max_jobs_per_process, exiting.")
                    return

        except Empty:
            logger.debug(f"{PROCESS_NAME} has no jobs to process, sleeping.")
            time.sleep(settings["empty_queue_sleep_time"])
            continue

    if running_tasks:
        asyncio.gather(*running_tasks)

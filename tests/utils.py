import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from quasiqueue import QuasiQueue, Settings

logger = logging.getLogger(__name__)


class QuickTestSettings(Settings):
    graceful_shutdown_timeout: float = 0.2
    save_dir: str


class StopTestException(Exception):
    pass


def get_pids_from_results(results):
    return {result["pid"] for result in results["files"].values()}


async def run_and_gather(settings: Settings, async_preferred=True) -> QuasiQueue:
    qq = QuasiQueue(
        name="testing",
        reader=_reader if async_preferred else _reader_sync,
        writer=_writer,
        context=_context if async_preferred else _context_sync,
        settings=settings,
    )

    try:
        await qq.main()
    except StopTestException:
        ...

    test_output = {"files": {}, "missing": []}
    test_files = list(Path(settings.save_dir).glob("*.output"))
    for file in test_files:
        with open(file) as f:
            test_output["files"][file.name] = json.load(f)

    for i in range(50):
        if f"{i}.output" not in test_output["files"]:
            test_output["missing"].append(i)
    return test_output


async def _context(settings: Dict[str, Any]):
    return {"settings": settings}


def _context_sync(settings: Dict[str, Any]):
    return {"settings": settings}


async def _writer(desired: int, settings: Dict[str, Any]):
    for i in range(50):
        yield i
    await asyncio.sleep(2)
    raise StopTestException("End Run")


async def _reader(item: str | int, settings: Dict[str, Any], ctx: Dict[str, Any]):
    with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
        json.dump({"pid": os.getpid()}, f)


def _reader_sync(item: str | int, settings: Dict[str, Any], ctx: Dict[str, Any]):
    with open(Path(settings["save_dir"]) / f"{item}.output", "w") as f:
        json.dump({"pid": os.getpid()}, f)

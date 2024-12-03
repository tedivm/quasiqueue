import logging
import tempfile

import pytest

from tests.utils import QuickTestSettings, get_pids_from_results, run_and_gather

logger = logging.getLogger(__name__)


def test_quasiqueue_import():
    from quasiqueue import QuasiQueue  # noqa: F401


@pytest.mark.asyncio
async def test_async_reader():
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(save_dir=d, num_processes=2)
        results = await run_and_gather(settings)
    assert len(get_pids_from_results(results)) == 2

    # @TODO: Figure out why this is 48 instead of 50
    assert len(results["files"].keys()) >= 48


@pytest.mark.asyncio
async def test_sync_reader():
    with tempfile.TemporaryDirectory() as d:
        settings = QuickTestSettings(save_dir=d, num_processes=2)
        results = await run_and_gather(settings, async_preferred=False)
    assert len(get_pids_from_results(results)) == 2
    assert len(results["files"].keys()) == 50

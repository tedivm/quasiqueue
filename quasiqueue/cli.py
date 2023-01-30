import asyncio
from importlib import import_module
from logging import getLogger
from typing import Callable

import click
import typer

from .runner import QueueRunner

logger = getLogger(__name__)
app = typer.Typer()


def get_function_from_string(mod_path: str) -> Callable:
    if not ":" in mod_path:
        raise ValueError("Module paths require variable.")
    try:
        module_path, variable = mod_path.split(":")
        module = import_module(module_path)
    except:
        logger.exception(f"Unable to load module from path: {mod_path}")

    return getattr(module, variable)


@app.command()
@click.option("--name", default="queue")
def run(reader: str, writer: str, context: str | None = None, name: str = "queue"):
    params = {
        "name": name,
        "reader": get_function_from_string(reader),
        "writer": get_function_from_string(writer),
    }

    if context:
        params["context"] = get_function_from_string(context)

    runner = QueueRunner(
        name=name,
        reader=get_function_from_string(reader),
        writer=get_function_from_string(writer),
        context=get_function_from_string(context) if context else None,
    )
    asyncio.run(runner.main())


if __name__ == "__main__":
    app()

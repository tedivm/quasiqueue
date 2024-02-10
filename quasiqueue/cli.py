import asyncio
from importlib import import_module
from logging import getLogger
from typing import Annotated, Callable

import click
import typer

from . import __version__
from .runner import QueueRunner

logger = getLogger(__name__)
app = typer.Typer()


def get_function_from_string(mod_path: str) -> Callable:
    if ":" not in mod_path:
        raise ValueError("Module paths require variable.")
    try:
        module_path, variable = mod_path.split(":")
        module = import_module(module_path)
    except:
        logger.exception(f"Unable to load module from path: {mod_path}")
        raise

    return getattr(module, variable)


@app.command()
@click.option("--name", default="queue")
def run(
    reader: Annotated[str, typer.Argument()],
    writer: Annotated[str, typer.Argument()],
    context: Annotated[str, typer.Argument()] = "",
    name: Annotated[str, typer.Argument()] = "queue",
):
    params = {
        "name": name,
        "reader": get_function_from_string(reader),
        "writer": get_function_from_string(writer),
    }

    if len(context) > 0:
        params["context"] = get_function_from_string(context)

    runner = QueueRunner(
        name=name,
        reader=get_function_from_string(reader),
        writer=get_function_from_string(writer),
        context=get_function_from_string(context) if context else None,
    )
    asyncio.run(runner.main())


@app.command()
def version():
    typer.echo(__version__)


if __name__ == "__main__":
    app()

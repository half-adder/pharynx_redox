import logging
import multiprocessing as mp
from pathlib import Path

import click

mp.set_start_method("spawn", force=True)


@click.group()
@click.option("--debug/--no-debug", default=False)
def cli(debug):
    """Useful scripts for analyzing ratiometric microscopy data"""
    if debug:
        loglevel = logging.DEBUG
    else:
        loglevel = logging.INFO

    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        level=loglevel,
        datefmt="%I:%M:%S",
    )

    click.echo("Debug mode is %s" % ("ON" if debug else "OFF"))


@cli.command()
@click.argument(
    "dir", type=click.Path(exists=True),
)
@click.option(
    "--gui/--command-line",
    default=False,
    help="use a graphical user interface, or run the whole pipeline without intervention on the command-line",
)
def analyze(dir, gui):
    """Analyze an experiment"""
    from pharedox import experiment

    exp = experiment.Experiment(Path(dir))
    if gui:
        from pharedox.gui.gui import App

        app = App(exp_=exp)
        app.run()
    else:
        exp.full_pipeline()


@cli.command()
@click.option(
    "--z",
    default="animal",
    type=str,
    help="dimension to place on the Z-axis. All other dimensions will be split into different stacks.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(exists=False),
    help="directory to put output tiffs into, defaults to current directory",
)
@click.argument("filepath", type=click.Path(exists=True))
def split_nc(filepath):
    """Split an xarray DataArray into multiple Tiffs"""
    raise NotImplementedError


@cli.command()
def create_settings():
    """Create a settings file using the default template and place in current directory"""
    import os
    import shutil

    path_to_example_settings = os.path.join(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
        "default-settings.yaml",
    )
    path_to_new_settings = os.path.join(os.getcwd(), "settings.yaml")

    shutil.copyfile(path_to_example_settings, path_to_new_settings)
    print(f"Generated settings file at {path_to_new_settings}")

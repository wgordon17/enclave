import logging
import sys

import click

from reconcile.operator_versions import reconcile as operator_versions_reconcile

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


@click.group()
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    help="Set the logging level.",
)
def cli(log_level: str) -> None:
    """Reconcile CLI."""
    logging.basicConfig(level=getattr(logging, log_level.upper()))


@cli.command()
@click.option("--operators", default="[]")
@click.option("--dry-run/--no-dry-run", default=False)
def operator_versions(operators: str, dry_run: bool) -> None:
    sys.argv = ["operator_versions.py", operators, str(dry_run)]
    operator_versions_reconcile()


@cli.command()
def mgmt_cluster_version() -> None:
    raise click.ClickException("mgmt-cluster-version is not implemented yet")


if __name__ == "__main__":
    cli()

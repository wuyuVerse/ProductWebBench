from __future__ import annotations

from ..construction.inventory import catalog, workspace


def add_catalog_args(parser) -> None:
    catalog.add_catalog_args(parser)


def run_catalog(args) -> None:
    catalog.run_from_args(args)


def add_extract_args(parser) -> None:
    workspace.add_extract_args(parser)


def run_extract(args) -> None:
    workspace.run_extract_from_args(args)

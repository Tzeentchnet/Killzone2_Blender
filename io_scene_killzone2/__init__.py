"""Blender 5.2 extension entry point for the Killzone 2 importer."""

import logging

_CONSOLE_HANDLER_NAME = f"{__package__}.console"

bl_info = {
    "name": "Killzone 2 Asset Importer",
    "author": "Killzone 2 Blender contributors",
    "version": (0, 5, 3),
    "blender": (5, 2, 0),
    "location": "3D View > Sidebar > KZ2 Tools; File > Import",
    "description": (
        "Import Killzone 2 models, textured maps, rigs, and animations"
    ),
    "category": "Import-Export",
}


def _configure_console_logging() -> None:
    logger = logging.getLogger(__package__)
    if not any(
        handler.get_name() == _CONSOLE_HANDLER_NAME
        for handler in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.set_name(_CONSOLE_HANDLER_NAME)
        handler.setFormatter(
            logging.Formatter("[KZ2] %(levelname)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _remove_console_logging() -> None:
    logger = logging.getLogger(__package__)
    for handler in tuple(logger.handlers):
        if handler.get_name() != _CONSOLE_HANDLER_NAME:
            continue
        logger.removeHandler(handler)
        handler.close()
    if not logger.handlers:
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def register() -> None:
    _configure_console_logging()
    from .operators import register as register_operators

    register_operators()


def unregister() -> None:
    from .operators import unregister as unregister_operators

    unregister_operators()
    _remove_console_logging()

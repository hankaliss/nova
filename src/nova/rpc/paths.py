"""Filesystem anchors for the sidecar."""

import os
from pathlib import Path

# Source checkout root (only meaningful when running from the repository).
REPO_ROOT = Path(__file__).resolve().parents[3]

# Where an installed package (packaging/arch) puts the bundled skills/ and
# agent.md.
INSTALLED_RESOURCES = Path("/usr/lib/nova")


def resources_dir() -> Path:
    """Directory holding the bundled ``skills/`` and ``agent.md``.

    ``$NOVA_RESOURCES_DIR`` wins; then the source checkout (it has
    ``skills/``); then the installed location.
    """
    override = os.environ.get("NOVA_RESOURCES_DIR")
    if override:
        return Path(override)
    if (REPO_ROOT / "skills").is_dir():
        return REPO_ROOT
    return INSTALLED_RESOURCES


RESOURCES_DIR = resources_dir()

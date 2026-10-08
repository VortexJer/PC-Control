"""Environment variables: PC_CONTROL_<NAME>, and PCSIGHT_<NAME> for compatibility with the previous installation."""
import os


def get(name, default=None):
    return os.environ.get("PC_CONTROL_" + name) or os.environ.get("PCSIGHT_" + name) or default

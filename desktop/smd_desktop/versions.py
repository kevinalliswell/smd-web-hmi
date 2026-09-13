"""SemVer ordering used only to reject ordinary installation downgrades."""

from .bundle import BundleError, verify_version


def _version_key(value: str) -> tuple:
    verify_version(value, value)
    core, separator, prerelease = value.partition("-")
    parts = tuple(int(part) for part in core.split("."))
    if not separator:
        return (*parts, 1, ())
    identifiers = []
    for identifier in prerelease.split("."):
        if not identifier or (identifier.isdecimal() and len(identifier) > 1 and identifier.startswith("0")):
            raise BundleError("SemVer 预发布标识无效")
        identifiers.append((0, int(identifier)) if identifier.isdecimal() else (1, identifier))
    return (*parts, 0, tuple(identifiers))


def compare_versions(left: str, right: str) -> int:
    """Return -1/0/1; rc.10 follows rc.9 and the stable version follows every RC."""
    lhs, rhs = _version_key(left), _version_key(right)
    return (lhs > rhs) - (lhs < rhs)

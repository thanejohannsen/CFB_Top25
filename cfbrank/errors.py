"""Exception types, each mapped to a CLI exit code (see cfbrank.cli)."""


class CfbRankError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(CfbRankError):
    """Configuration is missing, malformed, or internally inconsistent."""


class UpstreamUnavailable(CfbRankError):
    """The data source could not be reached, or returned unusable data."""


class DataQualityError(CfbRankError):
    """Data arrived but is too incomplete to rank from."""

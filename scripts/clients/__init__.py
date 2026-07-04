"""API client modules for external data sources."""

__all__ = ["FinraClient", "IBClient", "MenthorQClient", "UWClient"]


def __getattr__(name):
    if name == "FinraClient":
        from clients.finra_client import FinraClient
        return FinraClient
    if name == "IBClient":
        from clients.ib_client import IBClient
        return IBClient
    if name == "MenthorQClient":
        from clients.menthorq_client import MenthorQClient
        return MenthorQClient
    if name == "UWClient":
        from clients.uw_client import UWClient
        return UWClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

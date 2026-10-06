"""Errors that stop a run. Each has a stable `code` the agent can react to."""

from __future__ import annotations


class FBScoutError(Exception):
    code = "error"

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": str(self), "hint": self.hint}


class NotLoggedIn(FBScoutError):
    code = "not_logged_in"

    def __init__(self, message: str = "Not logged in to Facebook in the FB Scout browser profile."):
        super().__init__(message, hint="Run fb_login (CLI: `fbscout login`) and log in by hand in the Chrome window.")


class CheckpointHit(FBScoutError):
    code = "checkpoint"

    def __init__(self, message: str = "Facebook is showing a security checkpoint."):
        super().__init__(
            message,
            hint="Open the browser with fb_login, resolve the checkpoint by hand, then wait before searching again.",
        )


class TemporarilyBlocked(FBScoutError):
    code = "blocked"

    def __init__(self, message: str = "Facebook says this account is temporarily blocked from this feature."):
        super().__init__(message, hint="Stop for several hours (or a day). Do not retry in a loop.")


class BrowserUnavailable(FBScoutError):
    code = "browser_unavailable"


class ProfileInUse(FBScoutError):
    code = "profile_in_use"

    def __init__(self, message: str = "The FB Scout browser profile is already open in another window/process."):
        super().__init__(message, hint="Close the other FB Scout Chrome window (or wait for the other run) and retry.")


class Busy(FBScoutError):
    code = "busy"

    def __init__(self, message: str = "Another FB Scout run is already in progress."):
        super().__init__(message, hint="Wait for it to finish; only one run at a time.")

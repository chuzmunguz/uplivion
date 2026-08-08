"""Shared bounded username policy for API and operator entry points."""

import re


USERNAME_MAX = 25


def validate_username(raw):
    if not isinstance(raw, str):
        return False, None, "Username is required"
    username = raw.strip()
    if not username:
        return False, None, "Username is required"
    if len(username) > USERNAME_MAX:
        return False, None, f"Username must be at most {USERNAME_MAX} characters"
    if not re.fullmatch(r"[A-Za-z0-9._-]+", username):
        return False, None, "Username may contain only letters, digits, and . _ -"
    return True, username, None

"""Shared request validation contracts for the web API and operator CLI."""

import re


PASSWORD_MAX_BYTES = 72
NAME_MAX = 50


def text_value(value, label, max_length, required=False):
    """Return a bounded, stripped text field or a stable validation error."""
    if value is None:
        if required:
            return False, f"{label} is required", None
        return True, "", None
    if not isinstance(value, str):
        return False, f"{label} must be text", None
    cleaned = value.strip()
    if required and not cleaned:
        return False, f"{label} is required", None
    if len(cleaned) > max_length:
        return False, f"{label} is too long (max {max_length} characters)", None
    return True, "", cleaned


def validate_password_policy(password):
    if not isinstance(password, str):
        return False, "Password must be text"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return False, f"Password must be at most {PASSWORD_MAX_BYTES} bytes of UTF-8"
    if not re.search(r"[A-Z]", password):
        return False, "Password must include at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must include at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must include at least one digit"
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must include at least one special character"
    return True, ""

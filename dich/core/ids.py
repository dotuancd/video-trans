# Stripe-style ids: a prefix naming the kind, then a ULID — job_01JA…, task_…, att_…, art_…, call_…, upl_….
import os
import re
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid():
    """26 chars, sortable by creation time: 48 bits of milliseconds then 80 random bits."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def new_id(prefix):
    return f"{prefix}_{ulid()}"


def is_id(value, prefix):
    return isinstance(value, str) and re.fullmatch(f"{prefix}_[{CROCKFORD}]{{26}}", value) is not None

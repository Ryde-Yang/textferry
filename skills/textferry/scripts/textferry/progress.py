# SPDX-License-Identifier: AGPL-3.0-only
"""Small factual progress events for the calling agent."""
import json
import sys


def emit(stage, message, *, current=None, total=None):
    payload = {'event': 'progress', 'stage': stage, 'message': message}
    if current is not None:
        payload['current'] = current
    if total is not None:
        payload['total'] = total
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)

"""Customer Response Generation node (FR-RES-1..3, NFR-CMP-1/2).

Generates an empathetic, policy-consistent reply that reflects the outcome, presents the
standard-refund right for deflections, and never reveals risk/fraud reasoning.
"""

from __future__ import annotations

from agent.reply import compose_reply, scrub
from agent.state import ResolutionState


def responder(state: ResolutionState) -> dict:
    message = scrub(compose_reply(dict(state)))
    return {"customer_message": message}

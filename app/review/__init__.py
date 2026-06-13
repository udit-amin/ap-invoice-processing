"""Human review of flagged runs: the queue + effectful approve/reject/escalate.

An ``approve`` draws the matched PO down through the same race-safe path the
auto-decision uses; ``reject``/``escalate`` are record-only. Every action is
written to ``review_actions`` and the governance trail with the acting user.
"""

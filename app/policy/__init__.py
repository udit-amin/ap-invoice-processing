"""Live governance policy: read the single policy_config row and edit it at
runtime (manager-only). Edits are validated, version-bumped, and audited — the
decision engine reads policy fresh on every verdict, so a change takes effect on
the next run with no redeploy (acceptance criterion #5)."""

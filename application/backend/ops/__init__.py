"""Operational verification helpers run from the deploy scripts and the
acceptance evidence collector (W05-AUDIT).

These modules are the tested, authoritative implementations of the checks
`deploy/lib.sh` and `scripts/acceptance_evidence.sh` perform on an installed
stack. They only read deployment state (heartbeat rows, raw-data ages);
nothing here mutates data or talks to the Internet.
"""

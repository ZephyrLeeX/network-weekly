"""Runtime configuration loading.

Populated in W00-T005 with the explicit foundation settings (database URL,
runtime environment, data/report paths, Asia/Shanghai timezone, logging level,
worker identity and heartbeat interval). Kept as a package so production
config (SYSTEM_SPEC.md §26.1 `/etc/network-report`) has a single module home.
"""

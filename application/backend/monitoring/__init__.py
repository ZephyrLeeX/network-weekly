"""Wave 2 monitoring pipeline.

Modules here turn per-cycle collection outcomes into persisted monitoring
state: poll runs and raw metrics (W02-T001), utilization with counter
rebaseline (W02-T003), device reachability (W02-T004) and priority-interface
state (W02-T006) semantics, sustained-high detection (W02-T007), IRF
observation (W02-T008) and retention (W02-T009).

Everything downstream of the transports/collectors works on the Wave 1
DTOs and persistence primitives — no device protocol logic lives here
(SYSTEM_SPEC.md §7.4).
"""

# Architecture Taxonomy

The taxonomy makes agent comparisons explicit before metrics are interpreted.

## Required Fields

- `deployment_mode`: human-facing assistance, embedded automation, or hybrid.
- `workflow_integration`: the operational workflow being evaluated.
- `autonomy_level`: assistant, bounded autonomy, high autonomy, or fully automated within policy.
- `hitl_pattern`: none, approval, escalation, exception handling, or human final decision.
- `statefulness`: stateless, session stateful, workflow stateful, or persistent memory.
- `memory_strategy`: none, session trace, retrieval memory, long-term memory, or external system state.
- `composition`: single agent, single agent with tools, orchestrated agents, or multi-agent ecosystem.
- `decomposition_strategy`: manual, rule-based, planner-driven, learned, or scenario-defined.
- `allocation_mechanism`: static assignment, router-based, state-aware scheduling, or budget-aware scheduling.
- `routing_strategy`: none, rule-based, classifier, learned router, or policy orchestrator.
- `protocol_surfaces`: tool APIs, MCP, internal bus, workflow engine, or other integration surfaces.

## Assignment Rules

Use observable evidence where possible:
- configuration files for agent version, model, prompt, and orchestration policy
- workflow definitions for steps and boundaries
- traces for tool calls, routing, handoffs, and retries
- governance documentation for HITL and control boundaries

Do not infer a more advanced architecture than the evidence supports.

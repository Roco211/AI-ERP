# ADR 0008 — Future LangGraph runtime
Status: Accepted design; implementation deferred

The user selected LangGraph, LangChain components, LangSmith and PostgreSQL checkpointer for one ERP Assistant and tool registry. Bootstrap does not install or run the AI runtime or any business tools. AI will inherit RuntimeContext and invoke application commands in-process. No multi-agent product network.

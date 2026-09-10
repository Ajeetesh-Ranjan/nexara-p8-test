---
source_url: http://arxiv.org/abs/2507.06520v1
ingested: 2026-09-10
sha256: c83c2aa24ebf4cd7
---

Gradientsys: A Multi-Agent LLM Scheduler with ReAct Orchestration

We present Gradientsys, a next-generation multi-agent scheduling framework that coordinates diverse specialized AI agents using a typed Model-Context Protocol (MCP) and a ReAct-based dynamic planning loop. At its core, Gradientsys employs an LLM-powered scheduler for intelligent one-to-many task dispatch, enabling parallel execution of heterogeneous agents such as PDF parsers, web search modules, GUI controllers, and web builders. The framework supports hybrid synchronous/asynchronous execution, respects agent capacity constraints, and incorporates a robust retry-and-replan mechanism to handle failures gracefully. To promote transparency and trust, Gradientsys includes an observability layer streaming real-time agent activity and intermediate reasoning via Server-Sent Events (SSE). We offer an architectural overview and evaluate Gradientsys against existing frameworks in terms of extensibility, scheduling topology, tool reusability, parallelism, and observability. Experiments on the GAIA general-assistant benchmark show that Gradientsys achieves higher task success rates with reduced latency and lower API costs compared to a MinionS-style baseline, demonstrating the strength of its LLM-driven multi-agent orchestration.
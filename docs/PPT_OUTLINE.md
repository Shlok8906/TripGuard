# Slide outline (8 slides) with speaker notes

1. **Title** - TripGuard: a self-validating, disruption-aware trip planner. Name, roll no., course.
2. **Problem & users** - Student groups plan trips on tight budgets; LLM planners hallucinate prices/hours and can't recover when plans break.
3. **Solution** - Agents that plan, verify, retry and repair. One sentence per agent (Planner, Research, Executor, Critic, Disruption, Finalize).
4. **Architecture** - Insert `docs/architecture.png`. Call out: retry loop (red edge), tools, local LLM, human approval.
5. **What makes it agentic** - Autonomy (loop until validated), tool use (weather/routing/catalog with fallbacks), memory of constraints (closed places, locked legs), self-critique with machine-readable issues.
6. **Validation & safety** - 13 Critic checks; LLM text grounded (invented ₹ amounts rejected); infeasible requests reported honestly; human approval before export; synthetic data only.
7. **Live demo** (5 min, see `DEMO_SCRIPT.md`) - screenshot of trace with 3 attempts as backup.
8. **Limitations & future scope** - Synthetic data, greedy scheduler, 5 destinations; next: real APIs via MCP, preference memory, evaluation vs human planners.

Speaker note for slide 5: "The Critic does not trust the Executor. It recomputes every price from the catalog, so a bug or an LLM hallucination cannot slip through."

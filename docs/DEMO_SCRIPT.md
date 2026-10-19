# 5-minute demo script

Before presenting: start Ollama, check the sidebar says "Model ready: qwen3:4b", and generate your "any destination" place once
(for example Hampi) so it is saved and plans instantly.

| Time | Do | Say |
|---|---|---|
| 0:00 | Slide: problem | "Trip planners write plausible itineraries nobody checks. Mine proves its plan before showing it." |
| 0:30 | App open | "One chat box. A local model, qwen3:4b via Ollama, no paid API. Fares and timetables are synthetic sample data." |
| 0:45 | Click **Goa, 3 friends**, then Plan my trip | "Watch the agents: Planner, Research, Executor, then the Critic." |
| 1:15 | Point at Attempt 1, 2, 3 in the live trace | "The Critic rejected the first two plans as over budget. It recomputed every price itself. The Planner relaxed the strategy each time." |
| 2:00 | Itinerary tab | "Each day is a route: times, stops, cost, weather. Nothing overlaps and everything is open." |
| 2:30 | Budget tab | "Where the money goes, and how the plan was made to fit." |
| 3:00 | Checks tab | "13 independent checks, and exactly which data sources were live, saved or built-in." |
| 3:30 | Click **Hampi, any place** | "Not in my catalog. The model writes a destination profile, code validates and repairs it, and it is saved. Notice the AI-estimated warning: the Critic checks the plan against the profile, not against reality." |
| 4:10 | Click **Manali, tight budget** | "When it cannot be done it says so honestly, with how much it is short by." |
| 4:30 | Export tab: approve, download | "A human approves before anything is exported." |
| 4:45 | Slide: limitations and future scope | "Synthetic transport data, greedy scheduler, India only. Next: real booking APIs via MCP and preference memory." |

Backup if the laptop misbehaves: `python run_cli.py` shows the same flow in a terminal.

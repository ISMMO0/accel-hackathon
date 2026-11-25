"""Checks the LangGraph features on recorded data (no keys needed):

1. prints the hunt graph (Mermaid) and the chat graph
2. runs a hunt and shows that the Researchers ran in parallel (same step)
3. kills a hunt in the middle, then resumes it from the checkpoint: the finished steps are not redone

Run: MOCK_DELAY=0.3 ANTHROPIC_API_KEY= OPENAI_API_KEY= DATABASE_URL= .venv/bin/python -m scripts.graph_check
"""
import asyncio
import time

from app import bus
from app.agents.chat_graph import chat_graph
from app.agents.hunt_graph import _config, hunt_graph, resume_hunt, run_hunt
from app.providers import web
from app.store import get_store


async def new_research(query: str) -> str:
    store = get_store()
    r = await store.create_research(query=query, embedding=await web.embed(query), is_recorded=True)
    bus.new_research(r["id"])
    return r["id"]


async def main():
    await get_store().init()
    graph = hunt_graph()

    print("=== hunt graph ===\n" + graph.get_graph().draw_mermaid())
    print("=== chat graph ===\n" + chat_graph().get_graph().draw_mermaid())

    # 2. parallel Researchers
    rid = await new_research("A dating app for students")
    t0 = time.time()
    state = await run_hunt(rid, "A dating app for students")
    print(f"hunt done in {time.time() - t0:.1f}s: {len(state['companies'])} competitors, {len(state['facts'])} facts")
    steps = []
    async for snap in graph.aget_state_history(_config(rid)):
        steps.append((snap.metadata.get("step"), [t.name for t in snap.tasks]))
    for step, tasks in sorted(s for s in steps if s[1]):
        print(f"  step {step}: {', '.join(tasks)}")

    # 3. crash in the middle, then resume
    rid = await new_research("I want to open a specialty coffee shop in Paris 17e")
    task = asyncio.create_task(run_hunt(rid, "I want to open a specialty coffee shop in Paris 17e"))
    while True:  # wait until the Researchers' step is saved, then "crash"
        await asyncio.sleep(0.2)
        snap = await graph.aget_state(_config(rid))
        if snap.next and snap.next[0] == "analyst":
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    snap = await graph.aget_state(_config(rid))
    print(f"\ncrashed before: {snap.next} · facts already saved in the checkpoint: {len(snap.values['facts'])}")
    def count(kind):  # events this research emitted so far, by type
        return sum(1 for e in bus._cache[rid] if e["type"] == kind)

    before = count("competitor_found"), count("fact_added")
    state = await resume_hunt(rid)
    after = count("competitor_found"), count("fact_added")
    print(f"resumed: {len(state['facts'])} facts, gap = {state['strategy']['headline'][:60]}...")
    print(f"Scout/Researcher events before resume {before}, after {after} -> "
          + ("not run again" if before == after else "RUN AGAIN"))
    await bus.flush()


if __name__ == "__main__":
    asyncio.run(main())

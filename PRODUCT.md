# Launch Spy

## What it is
Founders (and the VCs who back them) type a company or an idea. A team of AI agents finds the competitors, reads their websites, pricing pages, careers pages and community threads, and turns it into a market picture: who they are, what they charge, where they're weak, and how to beat them. Every competitor is then watched over time, and changes trigger alerts.

## Who uses it
- Primary: an early-stage founder sizing up a market before or after building.
- Secondary: a VC associate doing competitive diligence on a deal.

## Where it's seen
Built at Accel AI Innovate Paris (22 Oct 2026). First real audience: judges watching a projected live demo, so the hunt must read from across a room.

## Product truths
- Every fact is grounded: it links to the page and date it came from.
- Agents are visible while they work: the user sees who is doing what, live.
- Data lives in Aiven (Postgres + pgvector, Valkey, OpenSearch); agents run on Claude, with H Company browser agents for hard pages.

## Surfaces
- Hunt: live view of agents discovering and researching competitors.
- Explore: positioning chart, comparison table, competitor dossier.

## Assumptions (inferred from the brief, not confirmed)
- Tone: sharp, calm, analyst-grade. No hype words.

# Routing eval

After talking with Parsa about how OpenPoke decides which agent should get a message, I wanted to know whether that routing breaks down as agents pile up, and whether my registry design (stable agent IDs plus key-matched cards) actually helps. So I ran a stress test: 84 synthetic probes placed in worlds of 3, 10, 30 and 50 agents, against the original names-only router, the registry and a RAG router, all on Haiku 5.5.

I tried to keep it honest. I committed my predictions before any results existed, and a later addendum is marked as written before but committed after the results. A separate session that had seen neither the labels nor the code labelled a sample blind and agreed on all 27 items. Deliberate breaks checked that the eval could actually see the registry fail; when single breaks changed nothing, that is what showed me how the registry really works. A small pilot caught the harness's own mistake before the paid run: it produced no valid agent reports until I fixed it. Every paid call is logged raw.

The registry beat the baseline by 6 points at 30 agents and 10 at 50, but not for the reason I expected. It wins through stable agent IDs in the conversation history and the dry-run owner lookup, not through its cards; with both removed, the lead disappears. The baseline doesn't get worse per decision as agents accumulate, but it merges unrelated work when it creates agents: given its own roster, it reused an existing name in 189 of 209 cases, and those merges grow with the number of agents. RAG as built stays flat; it was my adapter, which added conversation context to its query, that made it decline. The registry asks too rarely when a message is genuinely ambiguous, inbound watcher events are rarely dispatched to their owner, and model-reported statuses crowd the hot set because agents almost never say they are done.

The limits are real. The messages and worlds are synthetic, everything ran on one model, all results are on dev probes after some labels were widened, and I ran no held-out set and no multi-turn sessions.

Files, schemas, commit timestamps and the one command that recomputes every number above are in [INDEX.md](INDEX.md).

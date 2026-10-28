# Finds the best Ollama models for this computer: reads the hardware, looks up candidates on ollama.com and the web, downloads the ones I pick, benchmarks them with Nova's real tools and shows a ranked report, then sets the winner as my chat model.

Use it when I ask for the best, a faster or a better model for my PC, or to
benchmark or compare models. Follow these steps and wait for me where it says:

1. Call `model_scout.hardware`.
2. Find candidates: `web.search` for recent recommendations for this much VRAM
   (if web search fails, call `model_scout.lookup` with no models), then
   `model_scout.lookup` with the names that look promising.
3. Propose 3 to 5 that fit (fits_vram "yes" or "maybe"), with tool support,
   each with its download size, plus the installed ones worth comparing. Say
   which need a newer Ollama and give me the update command; don't propose
   pulling those. Ask me which to download. Wait.
4. `model_scout.pull` each model I picked, one at a time.
5. `model_scout.benchmark` the new ones together with my current model (quick
   only if I ask). The report appears in the side panel by itself.
6. Explain the winner in two or three sentences without repeating the table,
   and ask whether to make it my chat model. Only on yes: `model_scout.set_default`.
7. Offer to free disk space with `model_scout.remove` for losers I downloaded,
   only if I agree, one model per call.

Never pull, benchmark, switch or remove without my go-ahead in this chat.

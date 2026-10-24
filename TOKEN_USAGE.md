# Token usage

## References

Design reference: LiteLLM's `Usage` and `PromptTokensDetailsWrapper`, especially mapping DeepSeek `prompt_cache_hit_tokens` and Anthropic cache reads to normalized cached input. No LiteLLM runtime dependency is added.

- https://github.com/BerriAI/litellm/blob/main/litellm/types/utils.py
- https://api-docs.deepseek.com/zh-cn/api/create-chat-completion

Sources inspected on 2026-10-22. Provider usage fields are read from actual API responses, never estimated from text length.

## Accounting

- Persistent numeric ledger in the existing SQLite database, scoped to the configured account; includes response attempts, failures/retries, model/provider, duration, and reported usage. No prompts, responses, image data, credentials, or full payloads are stored in this ledger.
- Input includes cached input. Output includes reasoning. Neither subset is added twice to total tokens. Prefer provider-reported total; derive input+output only when both are known.
- DeepSeek cache hits/misses and OpenAI-compatible cached input are normalized. Anthropic input adds its separately reported cache read/creation amounts exactly once.
- Unreported usage/cache-write/reasoning stays NULL and displays as unavailable. Failed requests without a usage receipt are not assumed free. Cache hit rate is token-weighted and uses only input requests whose cache-read fields were reported.
- Model calls across chat, vision, memory and Moments are captured by the shared engine. Search/fetch HTTP calls are counted separately, with tokens included only if the endpoint reports them. Streaming totals are recorded once from the final usage data, including an empty-choices usage chunk.
- Today / last 7 days / last 30 days / all-time views use Beijing calendar days. Model and daily breakdowns are available. Switching models or restarting does not erase the ledger.
- Accounting starts with this deployment. Earlier cache usage cannot be reconstructed reliably. No prices or monetary savings are invented.
- Authenticated endpoint: `GET /api/token-usage?period=today|7d|30d|all`. No reset or delete operation is exposed.

## Verification

Focused tests cover native cache normalization, missing values, Anthropic input semantics, persistence/account isolation, weighted cache rate, Beijing midnight, retries, nonfatal ledger failures, and streaming final chunks. Production verification must not insert synthetic usage into the real ledger.

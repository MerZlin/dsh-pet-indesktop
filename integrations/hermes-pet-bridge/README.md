# Hermes Agent Link bridge

This plugin forwards Hermes activity state to the desktop pet's existing
`agent_link.custom_agents` JSONL reader. It uses Hermes public plugin hooks and
has no third-party dependencies.

## Data boundary

Activity records contain only `ts`, `agent`, `inst`, `state`, and (while working) the
short tool name. Human approval records contain only `ts`, `agent`, the
`approval/request` or `approval/resolved` event, a per-plugin-boot `inst` UUID,
and the opaque request/session
IDs needed to show and dismiss the desktop pet's generic approval hint. They do
not contain the command, prompt, decision text, chat messages, tool arguments or
results, file paths, or credentials. The bridge suppresses activity-state events
for `api_server` sessions and their descendant subagents so the pet's own Chat replies are not covered by
start/finish bubbles; approval events remain enabled. Write failures are
swallowed so the bridge cannot interrupt Hermes. The bridge makes no network
requests.

The `inst` envelope identifies simultaneous Gateway/CLI writers for consumers
that support writer partitioning. The current custom-agent reader does not yet
forward this field into its owner shards (unlike the DSH-specific reader), so
this metadata alone does not prevent another process's idle from replacing busy
state. Do not treat pure `record_owner`/fold tests as end-to-end verification.
Within one process, active tool-call IDs keep a session busy until its last
parallel tool returns. Missing IDs retain legacy single-call behavior. State
aggregation and append share one lock so a stale snapshot cannot overwrite a
newer state. A failed tool emits a generic error while retaining any other
in-flight calls in memory.

Tools with a `turn_id` are accepted only after that turn's `pre_llm_call`.
Background memory/skill-review forks deliberately skip lifecycle hooks while
sharing their parent's session ID; their tool hooks must not resurrect a busy
state after the real turn ends. Track attestation and state by stable turn ID
(session IDs can rotate during compression), and remove it on end. Callers
without turn IDs retain the older single-session compatibility behavior.

Limitations: this bridge has no heartbeat, log rotation, or process-death cleanup.
A writer killed without an end hook can leave a stale busy shard in the custom
reader until the monitor is reset; the DSH-specific PID retirement path is not
provided by this custom bridge. This plugin does not fix the chat client's
changing API session IDs: that requires a stable per-chat session identifier in
the request transport, not an activity hook.

## Install on Windows

1. Copy this directory to `%LOCALAPPDATA%\hermes\plugins\dsh-pet-bridge`.
2. Enable it with `hermes plugins enable dsh-pet-bridge`.
3. In the pet's data `config.json`, add this item under `agent_link.custom_agents`:

   ```json
   {"key":"hermes","name":"Hermes","path":"C:\\Users\\<user>\\AppData\\Roaming\\dsh-pet-standalone-webm-chat\\agent-events\\hermes.jsonl"}
   ```

   The plugin creates the event directory on first write. For a different pet
   variant or location, set `DSH_PET_HERMES_EVENTS` in the Hermes process
   environment and use that same file path in the pet configuration.
4. Restart the pet and enable **Agent Link → Hermes** from the pet's menu.
5. Restart the Hermes Gateway after enabling the plugin. CLI sessions load the
   plugin on their next process start.

## Codex subscription for the pet's chat

The plugin is only for activity display; it does not provide model access.
For chat, configure Hermes API Server as the pet's OpenAI-compatible provider:

- Base URL: `http://127.0.0.1:8642/v1`
- Chat path: `/chat/completions`
- Model: the desired model ID when the API server's `direct_model_requests` option is enabled;
  otherwise use the virtual model `hermes-agent` for the gateway's default model
- API key: the existing Hermes `API_SERVER_KEY`

Keep the server bound to `127.0.0.1`, require a strong bearer key, and configure
`platform_toolsets.api_server: []` if the pet should be a text-only chat client.
Without that restriction, Hermes API Server can expose the configured agent
toolsets to callers. Enable API Server and restart the Hermes Gateway yourself;
the direct model option is `platforms.api_server.direct_model_requests: true`.
Restart the Hermes Gateway after changing these settings; this repository's
bridge plugin does not restart Hermes configuration.

The Codex OAuth credential stays in Hermes. The pet only stores the local API
Server bearer key in its normal secret store.

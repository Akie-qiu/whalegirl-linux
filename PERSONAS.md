# Character cards

The console's **角色与提示词** section now has a role-card library, a structured
editor, a complete-prompt editor and a compiled role-prompt preview. Desktop
uses a library/editor split; phones use horizontally scrollable cards above
the editor, with full-width touch controls and two save/activate buttons.

Built-in cards: 鲸鱼娘, 温柔倾听者, 元气聊天搭子, 冷静吐槽役.
Built-ins are copied before customization. Custom cards support name, emoji,
color, description, identity, personality, speaking style, interests, scenario,
private/group interaction styles, boundaries, examples and extra instructions.
The raw and structured versions are kept separately; the chosen mode controls
which one compiles into the active role prompt.

Saving changes a library draft; activation changes the global role for future
chat and Moments generation. Editing an active card retains its published
version until activation. Existing custom prompts are imported exactly, with
no configuration write during migration. Generic settings saves preserve the
active role; the legacy `/api/prompt` route remains compatible and imports its
new content as a separate card when it changes the prompt.

Cards are persisted in `persona-cards.json` beside the core settings. There is
no permanent-delete API: inactive custom cards can be archived and restored.
Active cards cannot be archived. Optimistic revisions prevent another browser
from silently overwriting newer edits. Unsaved browser edits warn before
switching cards or leaving the page. Preview compiles text without invoking
an AI model or sending a chat message.

Activation writes the already-used `system_prompt_override` setting and role
metadata. The chat hot path performs no extra card I/O or model requests.
Chat permissions, wake-up rules, model settings, sending limits, affection,
memories and Moments switches keep their existing values. Existing memories
continue across role changes; they are not separate fictional character lives.

Endpoints (authenticated):

- `GET /api/personas`
- `POST /api/personas/save` with `{id?, revision?, card}`
- `POST /api/personas/preview` with a card specification
- `POST /api/personas/{id}/actions` with `{op, revision}`;
  operations are `activate`, `archive`, `restore`.

Six focused checks cover migration, separate draft/activation, unrelated
settings preservation, stale revisions, archive/restore, prompt compilation
and authenticated/legacy API behavior. An isolated local browser environment
verifies actual copy/save/activate/reload/preview and a 390px phone layout.
No real chat or public Moments acceptance messages are sent by these checks.

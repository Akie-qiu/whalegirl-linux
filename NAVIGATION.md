# Grouped navigation

The original 18 anchors and their feature click handlers are retained, arranged after deferred feature scripts finish loading:

- Overview stays pinned at the top.
- Connection and conversations: login, wake rules, send limits, runtime protection.
- Persona and memory: character/prompt, chat habits, text/sticker limits, long-term memory, affection.
- Interaction and content: sticker studio, Moments.
- Models and usage: model configuration, web search, token statistics.
- Logs and records: runtime logs, processing records, full inbox.

Only one group is expanded at a time. The group choice is remembered locally; the current URL fragment opens and highlights its matching group. Keyboard-accessible native details/summary controls provide the second level. The desktop menu scrolls independently so connection/exit controls remain accessible.

At widths up to 680px, the sidebar becomes a compact navigation button with the current section name. The dropdown closes on navigation, outside click, or Escape. Original fragment URLs continue to work, and no panel form values are reset by navigation.

Deployment replaces only console.js, console.css and web.html after hash checks and backups. No services, native WeChat or reader processes restart, and no configuration or user data is changed.

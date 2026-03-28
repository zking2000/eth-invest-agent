---
name: eth-chat
description: "Reply to supported ETH watcher questions from inbound iMessage"
metadata:
  {
    "openclaw": {
      "emoji": "💬",
      "events": ["message:preprocessed"],
      "os": ["darwin"],
      "requires": { "bins": ["python3"] }
    }
  }
---

# ETH Chat Hook

Replies to supported ETH watcher questions from inbound iMessage by delegating
to `scripts/eth_watcher.py chat-query`.

Supported examples:

- `ETH`
- `Can I buy now?`
- `How far is price from the entry?`
- `Can I start with a small size?`
- `How is it performing now?`
- `Position status`
- `Why this view?`
- `HELP`

Chinese prompts are also supported by the parser.

Enable with:

```bash
openclaw hooks enable eth-chat
openclaw gateway restart
```

# The relay channel plugin

A Claude Code plugin holding one channel, `claude-voice`, the server in `src/claude_voice/channel.py`. The bridge starts its sessions with this plugin enabled for that session only, and with `--channels plugin:relay-channel@claude-voice`, so the relay's messages reach the session as channel events instead of through the courier. The plugin runs in place from this clone, so the clone must stay where it was when the marketplace was added.

Channels are a research preview: `--channels` loads only plugins on the channel allowlist, which a managed policy on the computer sets (`allowedChannelPlugins`, with `channelsEnabled`). Setting it up once, the marketplace added, the plugin installed and then disabled for the user, so sessions the owner starts don't load it, is in `docs/channel.md`.

# wechat-archive

Let your AI agent read your own WeChat history on your Mac, quickly, locally, and read-only.

```bash
wx with 张伟                     # your conversation with someone
wx said alex --since 7d          # what someone said this week, in every chat
wx search "中国银行"              # full-text search
wx inbox --kind link             # links you sent to yourself
```

## Setup

You need a Mac with Apple Silicon, [Homebrew](https://brew.sh), and WeChat for Mac 4.x.

```bash
git clone https://github.com/jetsonearth/wechat-archive.git && cd wechat-archive
./install.sh
```

Then open your agent (Claude Code, Codex, …) and say *"set up this skill"* (or *"帮我设置这个 skill"*). The installer has already added the skill, so the agent runs `wx doctor` and walks you through the rest. You can also do the steps yourself:

1. **Log in to WeChat for Mac.**
2. **Capture the database keys once.** Quit WeChat, then run `wx setup --capture`. A temporary copy of WeChat opens; click Enter Weixin. Your real WeChat is never modified and no `sudo` is needed.
3. **Bring your phone's history to the Mac.** WeChat for Mac only has what arrived after you logged in, and wx can only read what's on the Mac. On your phone, go to Me → Settings → General → Manage Chat History → Migrate → Migrate to computer, and choose all chats. Keep both devices on the same Wi-Fi.
4. **Check it.** Run `wx doctor`. It shows how far back your history goes and how much of your media is stored at full size.

Keep WeChat for Mac logged in afterwards, so new messages keep arriving.

## How it works

WeChat for Mac stores messages in encrypted SQLite databases. `wx` keeps the keys in `~/.wechat-archive`, opens WeChat's live databases read-only, and maintains a single local index there. Before each query it syncs only what's new, usually in well under a second. No database is ever copied, and the index is about 0.75 KB per message.

## Privacy

Everything stays on your Mac. The keys and the index are readable only by you (`chmod 600`). `wx` cannot send messages or change anything in WeChat. It is meant for your own account on your own Mac.

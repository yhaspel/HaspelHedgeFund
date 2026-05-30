# Telegram Notifications Setup

Scheduled runs can notify you on Telegram in addition to (or instead of) email.
You supply two things from your own Telegram account: a **bot token** and a
**chat id**. It takes about two minutes.

## 1. Create a bot with @BotFather

1. Open Telegram and search for **@BotFather** (the official bot with the blue
   checkmark).
2. Send `/newbot`.
3. Choose a **name** (any text) and a **username** that ends in `bot`
   (e.g. `my_hedgefund_alerts_bot`).
4. BotFather replies with a line like:

   > Use this token to access the HTTP API:
   > `123456789:AAExampleTokenStringHere`

   That long string **is your bot token.** Keep it private — anyone with it can
   post as your bot.

## 2. Get your chat id

A bot can only message a chat it knows about, so first send your bot any
message, then look up the chat id.

**Option A — direct messages (simplest):**

1. In Telegram, open your new bot and tap **Start** (or send it "hi").
2. Search for **@userinfobot**, start it, and it replies with your numeric
   **Id** — that is your `chat_id` for direct messages.

**Option B — a channel or group:**

1. Add your bot to the channel/group as a member (for channels, make it an
   **admin** so it can post).
2. Post any message in the channel.
3. Visit this URL in a browser, replacing `<TOKEN>` with your bot token:

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

   Find `"chat":{"id":-1001234567890,...}` in the JSON. That negative number is
   the channel/group `chat_id`.

## 3. Add the channel in the app

1. Go to **Settings → Notifications**.
2. Click **Add a channel**, choose **Telegram**.
3. Paste the **bot token** and **chat id**.
4. Click **Add channel**, then **Send test** — you should receive a "Hello from
   your hedge fund" message within a few seconds.

If the test fails, double-check that you pressed **Start** on the bot (Option A)
or that the bot is an admin of the channel (Option B), and that the token and
chat id have no extra spaces.

## 4. Use it on a schedule

When you create a **Scheduled run** (Schedules tab), pick this Telegram channel
under **Notify via**. You'll get a message only when a run surfaces something
material — a signal flip, a large confidence move, a first-time risk veto, or a
new unanimous-bullish call — not on every run.

> **Privacy note:** your bot token is stored server-side and never shown again
> in the UI (only the last four characters are displayed). Delete the channel
> any time to stop delivery.

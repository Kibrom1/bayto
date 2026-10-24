# Bayto pilot guide

For people trying Bayto on their own machine. It takes about 15 minutes to set up and 10 to see a full discussion.

Bayto has no sign-in. Run it on your own computer only and do not expose its ports to a network.

## 1. What you need

- macOS or Linux, with Docker running
- [uv](https://docs.astral.sh/uv/), Node 22 or newer, and PostgreSQL 16 (a Docker container is fine)
- The `claude` command-line tool, signed in. Bayto uses your Claude subscription, not an API key
- `sbx` (Docker Sandboxes), signed in. Agents run inside it, not on your computer

Check all of this in one go:

```
scripts/bayto-doctor.sh
```

It changes nothing and tells you how to fix anything missing.

## 2. Start it

If you have no Postgres yet:

```
docker run -d --name bayto-pg -p 5432:5432 \
  -e POSTGRES_USER=bayto -e POSTGRES_PASSWORD=bayto -e POSTGRES_DB=bayto postgres:16
```

Then:

```
scripts/bayto-up.sh
```

It prepares the database and starts the API and the web app. Open http://127.0.0.1:5173. Logs are in `.bayto-run/`. Ctrl-C stops everything.

The Services page in the app can also start and stop Postgres, the API and the optional trace viewer, and shows what is running.

## 3. Try it

1. On an empty task list, click **Try a sample discussion**. It creates a real task about release cadence.
2. Pick a mode (Debate is a good first one) and seat three or four agents. Check the estimated cost and set a dollar cap.
3. **Start session.** Watch the agents talk. Type into the box to steer them. While it runs you can add a document, a folder or a web page with **Context** in the right-hand column.
4. **End & synthesize.** You get a synthesis (and a minority report for debates). Use **Copy Markdown** or **Download .md**; **Download transcript** saves the whole conversation.
5. Try it on your own question next. You can create your own specialist agents with **Create agent**.

Build mode works on a repository you choose, on its own branch. A test command is required (Check repository suggests one, for example `uv run pytest` or `cd orchestrator && uv run pytest`). The developer and tester may run exactly that and nothing else; only the developer may also run the commands you list under "Other allowed commands" (one per line, for example `uv sync`). The setup page shows what each role can run. Permissions are fixed when the session starts, so a chat reply cannot grant one: change the task's settings and start a new session. Everything else is refused, and `git push`, `curl` and `sudo` are never allowed. The sandbox has no internet except the Claude API, so a command that downloads packages will fail; pick a repository whose tests need nothing installed, or tests that use only the standard library. It never pushes or merges for you. When it finishes you can open a pull request from the Change Set page; this needs the GitHub CLI (`gh`) signed in on your machine.

## 4. Things to know

- **Cost.** Sessions use your Claude subscription. The dollar cap stops a session before a turn that would cross it. The estimate on the setup page is rough.
- **Restarts.** If the app restarts while a discussion is running, the session is marked Interrupted. Use **Continue with the same team** in the room to carry on in a new session.
- **Running out of Claude usage.** The session pauses with the reason and, when Claude says so, the reset time; resume after it. On the setup page you can opt in to a local Ollama model answering a seat instead (needs `OLLAMA_HOST`); those answers are badged "Local model", are usually weaker, and Build sessions never use it. Not yet tried against a real limit.
- **Attached folders** are read as a text copy. Files that look like they hold passwords or keys are left out, but do not attach folders you would not paste into a chat.
- **Attached links** must be public web pages.
- **Exposing the port beyond localhost.** By default Bayto has no sign-in and is meant for your own machine. If you must reach it from elsewhere, set `BAYTO_API_TOKEN` (a long random string) before starting the orchestrator: every route except `/healthz`, `/app-config` and `/auth/login` then needs `Authorization: Bearer <token>` or the session cookie the web app's sign-in page sets. This is one shared secret, not user accounts, and **not a replacement for a proper reverse proxy**. You still need TLS in front (without it the token and cookie travel in clear text) and should keep `BAYTO_ALLOWED_HOSTS` set to your host name. Failed sign-ins are slowed down, and the token is never passed to the programs Bayto starts.

- **Search past discussions** on the task list finds words in earlier answers and syntheses (not attachments, private notes or archived sessions). Results open the room; they do not scroll to the message.
## 5. What we would like to hear

After a real session, tell us:

1. What were you trying to decide or make, and did the result help?
2. Where did you get stuck or confused, from install to the final result?
3. Did anything cost more or take longer than you expected?
4. What would make you use it again next week?

If something breaks, send the last 30 lines of `.bayto-run/api.log` and what you clicked.

**For whoever runs the pilot: an in-app feedback link.** Set `BAYTO_FEEDBACK_URL` before starting the orchestrator to show a small "Send feedback" link in the header. It must be an `https://` address (a form, a shared inbox page) or a `mailto:` address; anything else is ignored and a warning is logged. Unset means no link. In a room, an `https` destination receives `?session=<session id>` (only the id, never task text or attachments) so you can match a comment to a session; a `mailto:` link gets only the subject "Bayto pilot feedback". The link opens in a new tab. The app does not store or read what people send there.

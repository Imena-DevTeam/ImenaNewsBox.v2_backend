
# Contributing to ImenaNewsBox-API

Welcome to the team! This guide walks you through everything you need to go from "I have a laptop" to "my first pull request is merged." Read it once top to bottom, then keep it open as a reference.

**What this repo is:** the backend API for ImenaNewsBox, built with FastAPI, async SQLAlchemy, Alembic and pytest.

---

## Contents

1. [The 60-second version](#1-the-60-second-version)
2. [Before you start](#2-before-you-start)
3. [Get the code](#3-get-the-code)
4. [Set up your local environment](#4-set-up-your-local-environment)
5. [Your daily workflow](#5-your-daily-workflow)
6. [Branch names](#6-branch-names)
7. [Commit messages](#7-commit-messages)
8. [Before you push](#8-before-you-push)
9. [Opening a pull request](#9-opening-a-pull-request)
10. [Code review](#10-code-review)
11. [Keeping your branch up to date](#11-keeping-your-branch-up-to-date)
12. [Project conventions](#12-project-conventions)
13. [Database migrations](#13-database-migrations)
14. [Adding dependencies](#14-adding-dependencies)
15. [Secrets and security](#15-secrets-and-security)
16. [Git mistakes and how to fix them](#16-git-mistakes-and-how-to-fix-them)
17. [For maintainers](#17-for-maintainers)

---

## 1. The 60-second version

```bash
git clone https://github.com/<your-org>/ImenaNewsBox-API.git   # once
cd ImenaNewsBox-API
uv sync                                                         # install dependencies

git switch main && git pull                                     # start from fresh main
git switch -c feat/short-description                            # make your branch

# ...write code and tests...

uv run ruff check . --fix && uv run ruff format . && uv run pytest
git add <files> && git commit -m "feat(scope): what you did"
git push -u origin feat/short-description                       # then open a Pull Request on GitHub
```

The rules that matter most:

- **Never commit directly to `main`.** Every change goes through a pull request.
- **One branch = one task.** Keep it small and short-lived.
- **Tests and linting must pass** before you ask for a review.
- **Never commit secrets** (`.env`, keys, passwords, tokens).

---

## 2. Before you start

Install these once:

| Tool | Why | Check it works |
|---|---|---|
| **Git** | version control | `git --version` |
| **Python 3.11+** | the language | `python --version` |
| **uv** | installs dependencies and runs the project | `uv --version` |
| **A GitHub account** in our organization | to push branches and open PRs | ask the team lead for an invite |

Install uv:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Tell Git who you are (once per machine), using the same email as your GitHub account:

```bash
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

> **Windows users:** keep the project in a plain folder such as `C:\dev\`, **not** inside OneDrive, Dropbox or any synced folder. Syncing a git repo and a `.venv` causes slow syncs, file locks and sometimes a corrupted `.git`.

---

## 3. Get the code

Clone the repository (HTTPS shown; use the SSH URL instead if you have SSH keys set up):

```bash
git clone https://github.com/<your-org>/ImenaNewsBox-API.git
cd ImenaNewsBox-API
```

You don't need to fork the repo. You're a member of the organization, so you push branches straight to it.

---

## 4. Set up your local environment

**Step 1. Install dependencies** (this also creates the virtual environment in `.venv/`):

```bash
uv sync
```

**Step 2. Create your local config:**

```bash
cp .env.example .env              # macOS / Linux
Copy-Item .env.example .env       # Windows PowerShell
```

Open `.env` and set a real secret key. Generate one with:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Paste the output as `SECRET_KEY`. `.env` is git-ignored. Never commit it.

**Step 3. Create the database tables:**

```bash
uv run alembic upgrade head
```

**Step 4. Run the API:**

```bash
uv run fastapi dev app/main.py
```

Open http://127.0.0.1:8000/docs to see the interactive API docs. The server reloads automatically when you save a file.

**Step 5. Run the tests** to confirm everything works on your machine:

```bash
uv run pytest
```

If all tests pass, you're ready. If something fails on a fresh clone, tell the team. That's a bug in our setup, not in you.

---

## 5. Your daily workflow

```
 main  ●────────●────────────────────●─────────●   (always deployable, protected)
        \                           ↗
         ●───●───●───●  feat/upload-endpoint        (your branch, short-lived)
                      │
                      └─ push → open Pull Request → review → squash merge → delete branch
```

### Step by step

**1. Start from the latest `main`:**

```bash
git switch main
git pull
```

**2. Create your branch** (see [naming rules](#6-branch-names)):

```bash
git switch -c feat/upload-endpoint
```

**3. Do the work.** Commit early and often in small, logical pieces ([commit rules](#7-commit-messages)):

```bash
git add app/api/routes/uploads.py tests/test_uploads.py
git commit -m "feat(uploads): add endpoint to upload an image"
```

Prefer `git add <specific files>` over `git add .`, so you don't commit things by accident. Check what you're about to commit with `git status` and `git diff --staged`.

**4. Run the checks** ([see list](#8-before-you-push)).

**5. Push your branch:**

```bash
git push -u origin feat/upload-endpoint
```

The `-u` flag is needed only the first time. After that, plain `git push` works.

**6. Open a pull request** on GitHub ([details](#9-opening-a-pull-request)).

**7. Address review comments** by pushing more commits to the same branch. The PR updates automatically.

**8. After it's merged,** clean up:

```bash
git switch main
git pull --prune
git branch -D feat/upload-endpoint
```

Use a capital **`-D`** here. Because we squash merge, Git doesn't see your branch's commits on `main` and refuses the lowercase `-d` with "not fully merged." That's expected. Once GitHub shows the PR as merged, your work is safe on `main`, so `-D` is fine.

---

## 6. Branch names

Format: **`type/short-description`**, all lowercase, words separated by hyphens.

| Type | Use it for | Example |
|---|---|---|
| `feat` | a new feature | `feat/video-upload` |
| `fix` | a bug fix | `fix/login-token-expiry` |
| `refactor` | restructuring code without changing behavior | `refactor/split-auth-service` |
| `test` | adding or fixing tests only | `test/article-pagination` |
| `docs` | documentation only | `docs/update-readme` |
| `chore` | tooling, dependencies, config | `chore/bump-fastapi` |
| `hotfix` | urgent production fix | `hotfix/null-author-crash` |

If you have a ticket or issue number, include it: `feat/123-video-upload`.

Good names are short and say what the branch is for. Avoid `my-branch`, `test`, `stuff` or `john-work`.

---

## 7. Commit messages

We follow **Conventional Commits**:

```
type(scope): short summary in the imperative mood
```

```
feat(auth): add refresh token endpoint
fix(uploads): reject files larger than the limit
refactor(db): move session setup to its own module
test(articles): cover pagination edge cases
chore(deps): upgrade sqlalchemy to 2.0.x
docs: explain how to run migrations
```

Rules:

- **Imperative mood:** "add", "fix", "remove", not "added" or "fixes".
- **Under about 70 characters** in the first line, and no full stop at the end.
- **`scope` is optional** and names the area you touched (`auth`, `uploads`, `db`, ...).
- **One logical change per commit.** Don't mix a bug fix, a refactor and a new feature.
- Use the body for the *why* when it isn't obvious. Add a blank line, then explain.

---

## 8. Before you push

Run these three commands. They are the same checks a reviewer expects to be green:

```bash
uv run ruff check . --fix     # lint (catches bugs, unused imports, bad style)
uv run ruff format .          # auto-format
uv run pytest                 # tests
```

Quick checklist:

- [ ] Tests pass locally
- [ ] I added or updated tests for what I changed
- [ ] If I changed a database model, I created and reviewed a migration ([section 13](#13-database-migrations))
- [ ] No secrets, `.env` files, `.db` files or debug `print()` calls in my diff
- [ ] `git status` shows only the files I meant to change

---

## 9. Opening a pull request

On GitHub, click **Compare & pull request** after pushing your branch (or go to the *Pull requests* tab, then *New pull request*).

**Title:** use the same format as a commit message.
`feat(uploads): add endpoint to upload an image`

**Description:** use this template:

```markdown
## What
One or two sentences on what this PR does.

## Why
The problem it solves, or a link to the issue/ticket (Closes #123).

## How to test
1. Run the API
2. Call POST /uploads with an image
3. Expect a 201 and the file URL in the response

## Notes for the reviewer
Anything tricky, any trade-offs you made, anything you want a second opinion on.
```

Good pull requests are:

- **Small.** Aim for a change a reviewer can understand in about 15 minutes. If it's growing huge, split it into several PRs.
- **Focused.** One purpose per PR. Don't sneak in unrelated cleanups.
- **Self-reviewed.** Read your own diff on GitHub before asking anyone else to.
- **Complete.** Includes tests and, if relevant, the migration.

Not finished yet but want early feedback? Open it as a **Draft pull request**, and mark it *Ready for review* when it's done.

---

## 10. Code review

**If you're the author:**

- Request at least one reviewer (the team lead can tell you who owns which area).
- Respond to every comment, either with a change or a short explanation. Resolve a thread only once it's addressed.
- Push fixes as new commits during review. Don't rewrite history on a PR someone is actively reviewing.
- Don't take comments personally. Review is about the code, and everyone's code gets comments.

**If you're the reviewer:**

- Review promptly, since a waiting PR blocks a teammate.
- Be specific and kind. Explain *why*, and suggest an alternative when you can.
- Separate must-fix problems from optional suggestions (prefix the latter with `nit:`).
- Check for: correctness, tests, security, error handling, naming, and whether the change fits our [conventions](#12-project-conventions).

**Merging:**

- A PR needs at least one approval and all checks passing.
- We use **Squash and merge**, so each PR becomes one clean commit on `main`.
- The author merges their own PR once it's approved. Delete the branch afterwards.

---

## 11. Keeping your branch up to date

If `main` moved while you were working, bring your branch up to date:

```bash
git fetch origin
git rebase origin/main
```

If Git reports a **conflict**:

1. Open the conflicting files and look for `<<<<<<<`, `=======` and `>>>>>>>` markers.
2. Edit each file to keep the correct result, and delete the markers.
3. Run `git add <file>` for each fixed file.
4. Run `git rebase --continue`.
5. Run the tests again.

Changed your mind mid-way? `git rebase --abort` puts everything back as it was.

Since rebasing rewrites your branch's history, pushing afterwards needs a force. Use the safe version, and only on **your own** feature branch:

```bash
git push --force-with-lease
```

**Never force-push to `main`.**

---

## 12. Project conventions

### Folder structure

```
app/
├── main.py          # app creation, middleware, routers
├── core/            # config (settings) and security helpers
├── db/              # engine, session, Base, get_session dependency
├── models/          # SQLAlchemy tables (database shape)
├── schemas/         # Pydantic models (API request/response shape)
└── api/
    ├── deps.py      # reusable dependencies (current user, DB session)
    └── routes/      # one file per resource (auth.py, users.py, ...)
tests/               # mirrors the app structure
migrations/          # Alembic migrations (committed to git)
```

New resource? Add a model, a schema, a router and tests, plus a migration if the database changes.

### Code rules

- **Type hints everywhere.** FastAPI and Pydantic depend on them.
- **Keep models and schemas separate.** Never return a SQLAlchemy model without a `response_model`, or you risk leaking fields like password hashes.
- **Use separate input and output schemas** (`UserCreate` vs `UserRead`).
- **No blocking calls inside `async def`.** Don't use `requests`, `time.sleep` or sync database drivers there. Use `httpx`, `await asyncio.sleep` and async libraries. For unavoidable blocking or CPU-heavy work, use `run_in_threadpool`, and for heavy jobs use a background worker.
- **Use dependency injection** (`Depends`) for the database session, the current user and shared logic.
- **No hardcoded config.** Everything configurable goes through `app/core/config.py` and environment variables.
- **Use proper HTTP errors** (`HTTPException` with the right status code), not a 200 with an error message.
- **Use the `logging` module,** not `print`.
- **Every endpoint needs tests,** covering the success path and at least the main failure cases (bad input, unauthorized, not found).

### Naming

| Thing | Style | Example |
|---|---|---|
| files, functions, variables | `snake_case` | `get_current_user` |
| classes (models, schemas) | `PascalCase` | `ArticleCreate` |
| constants | `UPPER_SNAKE_CASE` | `MAX_UPLOAD_SIZE` |
| database tables | plural `snake_case` | `articles` |

---

## 13. Database migrations

We use **Alembic**. Changing a model does *not* change the database until a migration is applied.

When you change a model in `app/models/`:

```bash
# 1. generate a migration by comparing models to the database
uv run alembic revision --autogenerate -m "add video table"

# 2. OPEN the new file in migrations/versions/ and review it
#    (autogenerate can miss things, e.g. a column rename looks like drop + add)

# 3. apply it
uv run alembic upgrade head
```

Rules:

- **Always review the generated migration** before committing it.
- **Commit the migration in the same PR** as the model change.
- **Never edit a migration that has already been merged.** Create a new one instead.
- **Test the downgrade** works: `uv run alembic downgrade -1`, then `upgrade head` again.
- After pulling someone else's work, run `uv run alembic upgrade head` to catch up.
- **Two migrations created at the same time** can produce "multiple heads." Check with `uv run alembic heads`, and fix with `uv run alembic merge heads -m "merge heads"`.

---

## 14. Adding dependencies

Use `uv`, not plain `pip`:

```bash
uv add httpx                 # runtime dependency
uv add --dev pytest-cov      # development-only dependency
uv remove httpx              # remove one
```

Commit **both** `pyproject.toml` and `uv.lock`. The lock file makes everyone's install identical.

Before adding a package, ask yourself whether it's actively maintained, whether the standard library or an existing dependency already does the job, and whether it's worth the added weight. Mention new dependencies in your PR description.

---

## 15. Secrets and security

- **Never commit** `.env`, API keys, passwords, tokens, private keys or database files. They're listed in `.gitignore`, but check your diff anyway.
- Commit `.env.example` with **fake placeholder values** when you add a new setting.
- **If you commit a secret by accident,** tell the team lead *immediately*, even if you already deleted it in a later commit. It stays in git history, so the secret must be rotated (replaced). Don't just try to hide it.
- Validate all input through Pydantic schemas, and never build SQL by concatenating strings.
- Spotted a security problem? Tell the team lead privately instead of opening a public issue.

---

## 16. Git mistakes and how to fix them

Everyone makes these. None are fatal.

**I committed to `main` by accident (and haven't pushed):**

```bash
git switch -c feat/my-work           # your commit is now safely on a new branch
git switch main
git reset --hard origin/main         # put local main back to match GitHub
git switch feat/my-work              # carry on
```

**I'm on the wrong branch and have uncommitted changes:**

```bash
git stash                            # set the changes aside
git switch correct-branch
git stash pop                        # bring them back
```

**Undo my last commit but keep the changes:**

```bash
git reset --soft HEAD~1
```

**I staged a file I didn't mean to:**

```bash
git restore --staged path/to/file
```

**Throw away my uncommitted changes in one file** (this can't be undone):

```bash
git restore path/to/file
```

**My commit message has a typo (not pushed yet):**

```bash
git commit --amend
```

**I'm lost and want to see what's going on:**

```bash
git status
git log --oneline --graph --decorate -15
```

Still stuck? Don't try random commands. Ask for help *before* running anything with `--force` or `reset --hard`.

---

## 17. For maintainers

Set these once in **GitHub, then Settings, then Branches (or Rulesets), then the `main` branch rule**:

- [ ] Require a pull request before merging
- [ ] Require at least 1 approval
- [ ] Dismiss stale approvals when new commits are pushed
- [ ] Require status checks to pass (add the CI checks once CI exists)
- [ ] Require branches to be up to date before merging
- [ ] Block force pushes and branch deletion on `main`
- [ ] Allow **squash merging** only
- [ ] Automatically delete head branches after merge

Good next steps for the repo: a `.github/pull_request_template.md` containing the template from [section 9](#9-opening-a-pull-request), a `CODEOWNERS` file to auto-request reviewers, and a CI workflow that runs the three commands from [section 8](#8-before-you-push).

---

## Getting help

Stuck on setup, unsure how to structure a feature, or not sure what to work on? Ask in the team channel or tag the team lead on your PR. Asking early is always better than being stuck for hours.

Thanks for contributing! 🎉
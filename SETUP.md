# Setup

A self-updating, terminal-style GitHub profile README (inspired by
[Andrew6rant](https://github.com/Andrew6rant/Andrew6rant)). A Python script queries
the GitHub GraphQL API and rewrites two SVGs; a scheduled GitHub Action keeps them fresh.

## 1. Create the special repo

On GitHub, create a **public** repository whose name is **exactly your username**
(e.g. user `octocat` → repo `octocat/octocat`). GitHub shows that repo's `README.md`
on your profile page. Push these files there.

## 2. Create a personal access token

1. GitHub → **Settings → Developer settings → Personal access tokens → Tokens (classic)**.
2. **Generate new token (classic)** with scopes: `repo` and `read:user`.
3. Copy the token.

## 3. Add the token as a secret

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

- Name: `ACCESS_TOKEN`
- Value: the token from step 2

> The default `GITHUB_TOKEN` is **not** enough — the LOC query needs a classic PAT.

## 4. (Optional) Set your birthday for "Uptime"

Same page → **Variables** tab → **New repository variable**

- Name: `BIRTHDAY`
- Value: `YYYY-MM-DD` (e.g. `1998-04-23`)

If unset, "Uptime" counts from your GitHub account creation date instead.

## 5. Run it

Go to the **Actions** tab → **Update profile stats** → **Run workflow**.
After it finishes it commits the updated `dark_mode.svg` / `light_mode.svg`.
Then it runs automatically twice a day.

## Customize

Edit the static lines (`OS`, `Editor`, `Languages`, the `user@github` header) directly
in `dark_mode.svg` and `light_mode.svg`. The auto-filled values live between
`<!--key-->...<!--/key-->` comment markers — leave those markers in place.

## Run locally

```bash
pip install -r requirements.txt
ACCESS_TOKEN=ghp_xxx BIRTHDAY=1998-04-23 python today.py
```

The first run is slow (it walks every commit for the line count); results are cached
in `cache/loc_cache.json`, so later runs only re-scan repos that changed.

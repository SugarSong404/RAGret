---
name: bcecli
description: >-
  Retrieve grounded passages from a remote bcecli HTTP API using curl. Use for
  bcecli, semantic search over an indexed corpus, or when the user points you at
  their retrieval URL. Search by default; do not build indexes unless they
  explicitly ask.
---

# bcecli

## What to do

1. **Auth:** ask the user to declare `BCECLI_API_KEY` in the environment first (do not request the raw key in chat). Use `X-API-Key: $BCECLI_API_KEY` (or `Authorization: Bearer $BCECLI_API_KEY`) for retrieval routes. If the server requires account login for other actions, `POST /api/auth/login` returns a session `token`.
2. **Retrieve:** Run `curl` against the user’s **base URL** (host, port, **index name**). Listing and search use **GET**.
3. **List indexes for this API key (if needed):** `curl -sS -H "X-API-Key: $BCECLI_API_KEY" "$BASE/api/subscribe-indexes"`.
4. **Search:**  
   `curl -sS -G "$BASE/api/search/INDEX_NAME" -H "X-API-Key: $BCECLI_API_KEY" --data-urlencode "query=…"`  
   Parse JSON **`result`** (or `format=text`).

If the user does not explicitly provide a **base URL**, infer it from context first; if still unclear, default to `http://127.0.0.1:8765`.
Ask for anything missing: **base URL** and **index name**. If `BCECLI_API_KEY` is missing, ask the user to set the environment variable locally and rerun.

## Full example

```bash
# 1) Set the API root (no trailing slash)
export BASE_URL='https://bcecli.example.com'

# 2) User declares API key in local environment first
# export BCECLI_API_KEY='sk-...'

# 3) List KBs in API key scope (owned + subscribed)
curl -sS -H "X-API-Key: ${BCECLI_API_KEY}" "${BASE_URL}/api/subscribe-indexes"

# 4) Search index "product_docs"
curl -sS -G "${BASE_URL}/api/search/product_docs" \
  -H "X-API-Key: ${BCECLI_API_KEY}" \
  --data-urlencode "query=How do we handle refunds within 30 days?"

# Response is JSON: read .result (multi-line string of ranked passages).
# Plain-text variant:
curl -sS -G "${BASE_URL}/api/search/product_docs" \
  -H "X-API-Key: ${BCECLI_API_KEY}" \
  --data-urlencode "query=How do we handle refunds within 30 days?" \
  --data-urlencode "format=text"
```

Primary retrieval mode for this skill is API key via `BCECLI_API_KEY`. Use login/session only when the task explicitly needs account-level operations.

## Rules

- **Default:** answer from retrieval output; cite `source:` when useful.
- If retrieval output contains URL(s), explicitly show those URL(s) to the user.
- **Do not** run indexing or registry changes to answer a normal question—only if the user clearly asks to build or refresh an index (they handle that on their side).
- **Do not** use POST/DELETE for **retrieval** unless the user explicitly asks to change data; **login** uses `POST /api/auth/login` when needed.

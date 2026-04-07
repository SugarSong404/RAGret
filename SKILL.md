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

1. **Retrieve:** Run `curl` in the terminal against the user’s **base URL** (they must give you host, port if any, and **index name**). Use **GET** only.
2. **List indexes (if needed):** `curl -sS "$BASE/api/indexes"` (add `Authorization: Bearer …` if they use a token).
3. **Search:**  
   `curl -sS -G "$BASE/api/search/INDEX_NAME" --data-urlencode "query=…"`  
   Parse JSON and answer from the **`result`** field (or use `format=text` if they prefer plain text).

If the user does not explicitly provide a **base URL**, infer it from context first; if still unclear, default to `http://127.0.0.1:8756`.
Ask for anything missing: **index name**, **bearer token** (use env vars in the command, do not paste secrets into the chat).

## Full example

```bash
# 1) Set the API root (no trailing slash)
export BASE_URL='https://bcecli.example.com'

# 2) Optional: if the server requires auth
export BCECLI_API_TOKEN='your-secret-token'

# 3) List registered index names
curl -sS -H "Authorization: Bearer ${BCECLI_API_TOKEN}" "${BASE_URL}/api/indexes"

# 4) Search index "product_docs" for a natural-language question
curl -sS -G "${BASE_URL}/api/search/product_docs" \
  -H "Authorization: Bearer ${BCECLI_API_TOKEN}" \
  --data-urlencode "query=How do we handle refunds within 30 days?"

# Response is JSON: read .result (multi-line string of ranked passages).
# Plain-text variant:
curl -sS -G "${BASE_URL}/api/search/product_docs" \
  -H "Authorization: Bearer ${BCECLI_API_TOKEN}" \
  --data-urlencode "query=How do we handle refunds within 30 days?" \
  --data-urlencode "format=text"
```

If the server has **no** token, drop the `-H "Authorization: Bearer …"` lines.

## Rules

- **Default:** answer from retrieval output; cite `source:` when useful.
- If retrieval output contains URL(s), explicitly show those URL(s) to the user.
- **Do not** run indexing or registry changes to answer a normal question—only if the user clearly asks to build or refresh an index (they handle that on their side).
- **Do not** use POST or DELETE on this API.

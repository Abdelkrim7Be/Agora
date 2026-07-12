# Token Encryption

Agora stores Gmail OAuth tokens as envelope-encrypted `*.enc` blobs when token encryption is enabled.

## Key source

Set `AGENT_TOKEN_ENCRYPTION_KEY_FILE` to a mounted secret file in production. The file may contain either:

- A raw secret string for a single active key.
- JSON with rotation metadata, for example:

```json
{"active_key_id":"2026-07","keys":{"2026-06":"old-secret","2026-07":"new-secret"}}
```

`AGENT_TOKEN_ENCRYPTION_KEY` remains available for local development back-compat, but production should not keep the secret in `.env`.

## Rotation

1. Add the new key id and secret to the mounted secret file.
2. Flip `active_key_id` to the new key.
3. Existing tokens continue to decrypt with their stored `key_id`.
4. The next successful token write re-wraps that token under the new `key_id`.
5. Remove the old key only after every active token has been re-wrapped or the affected mailbox has been re-authorized.

## Recovery

If the required master key is lost, the stored token cannot be decrypted. The recovery path is explicit: delete the stored token and reconnect Gmail through OAuth.

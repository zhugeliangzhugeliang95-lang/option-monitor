# Logging and secret redaction

`backend/app/logs.py` sets up logging. The worker calls `configure()` in `main()`; `api.py` and `gateway.py` call it at import, after uvicorn has set up its own loggers.
- Level comes from `LOG_LEVEL` (default INFO). Output goes to stdout as `%(asctime)s %(levelname)s %(name)s %(message)s`.
- `configure()` is idempotent:
  - It adds one handler to `app`.
  - It adds a handler to root **only if root has none**; existing root handlers are wrapped, never duplicated.
  - It wraps every handler of root, `app`, `futu`, `FTFileLog`, `FTConsoleLog` (the Futu SDK's own loggers), `uvicorn`, `uvicorn.error` and `uvicorn.access` in a redacting formatter.
- Redaction runs on the final formatted line, so it covers %-args, `exc_info` tracebacks (including the last `ValueError: …` line) and `stack_info`.
- Code must still never log credentials, env values, config payloads or request params. Redaction is a last line of defence.

## Masked
- Labelled values written as `k: v`, `k = v`, `k=v`, `'k': 'v'`, `"k": "v"` or `f(k='v')`. This also works after another label, e.g. `ValueError: password='x'` and `cfg: password='a b'`. Labels are matched **token-aware**: they are split on `_ - .` and camelCase, then whole tokens or joined adjacent tokens are checked.
  - password, passwd, passphrase, pass, passcode, pwd, pw, otp, auth (also `PassWord`, `pass_word`), token, secret, authorization, cookie, credential(s)
  - `key` alone, or `key` after api/secret/private/access/auth/client/signing/encryption/session/rsa/dsa/ec/ssh/pem/gpg/pgp/hmac/jwt/master/app/sign (e.g. `api_key`, `apikey`, `apiKey`, `FUTU_RSA_KEY`, `rsa_private_key`)
  - acc/acct/account followed by id/no/num/number at any position, or as the last token (`FUTU_ACC_ID`, `account-id`), plus `user_id` and `session_id`
- Separator `=>` too (`password => hunter2`). Quoted values honour backslash-escaped quotes (`'hun\'ter2'` is masked whole).
- Space-separated values after password/passwd/pwd/pw/token/secret (`pwd hunter2`).
- Bare account numbers (4+ digits) after account/acc/acct (`account 28001234`).
- `Authorization:` / `Proxy-Authorization:` with any scheme (Bearer, Basic, token, Digest with all its params, NTLM, …): in header style the rest of the line is masked; in a quoted repr, the whole quoted value. Standalone `Bearer/Basic <token>` is masked too.
- `name=value` in URLs or query strings, even inside quoted reprs, and `scheme://user:pass@` userinfo.

## Performance and long lines
Redaction runs synchronously inside the worker thread, so it must be linear-time.
- Label names may be any length but cannot start right after `\w`, `.` or `-`, and they are matched possessively, so the scan stays linear. Only the **last 64 characters** of a name are classified, so a long dotted path ending in `PASSWORD` is still masked. A sensitive word that appears only before those last 64 characters is not detected. A precompiled pre-check skips names that contain no sensitive word fragment (ignoring `_ . -`) without leaving the regex engine. Unquoted values are matched up to 256 chars; when the label is sensitive the whole rest of the value is masked. URL schemes are bounded to 16 chars and userinfo parts to 256.
- Order is **truncate, then redact**. A whole record longer than **65536** chars (`MAX_TOTAL`) keeps only its first 65536 chars. Any single line longer than **8192** chars (`MAX_LINE`) keeps only its first 8192. Dropped tails are never printed, so they cannot leak. The kept text is redacted, then the visible `...[truncated N chars]` marker is appended. A value cut at a boundary (quoted or unterminated) is masked to the end of the kept text. When a line is truncated, each line is redacted on its own, so a label at the end of one line won't mask a value on the next line in that case.
- Multi-line records up to the cap (including 1 MB inputs, which are capped first) are redacted in under 50 ms.
- Tests: 100,000-char lines of `a.`, `a-`, `a:`, `a=`, `x://`, `=`, `:`, `/`, `@`, quotes, spaces and others each finish in under 50 ms (best of 3). The redaction also scales linearly with truncation disabled. Measured worst case is about 19 ms (`a:`).
- Not masked because of the bounds: a sensitive word that appears only before the last 64 characters of a label name.

## Not masked on purpose
These are tested: `passport`, `compass`, `bypass`, `monkey`, `keys`, `keyboard`, `account_balance`, `optionSessionOpen`, `authority`, `symbol=…`, `status=…`. A token like `pass` is only matched when it's a whole token.

## Known unmasked cases (not covered)
- URL-encoded secrets: `password%3Dhunter2` and `%27`-quoted values aren't decoded.
- List/tuple forms where the label and value are separate items: `('password', 'hunter2')`, `['pwd', 'x']`.
- Prose with no `:`/`=`: `password is hunter2`. (`password hunter2` is masked; `pass hunter2` isn't, to avoid masking ordinary text like "tests pass".)
- Bare secrets with no label: a token or password printed alone.
- Handlers added **after** `configure()` runs: uvicorn `--reload` workers re-running dictConfig, the Futu SDK's `FTLog.fontColor()` (unused), libraries that add handlers lazily. **Don't use `--reload` in production.**
- Anything written straight to stdout/stderr rather than through `logging`, such as `print` or C-level output.

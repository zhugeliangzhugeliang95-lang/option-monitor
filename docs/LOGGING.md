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
  - password, passwd, passphrase, pass, pwd, pw (also `PassWord`, `pass_word`), token, secret, authorization, cookie, credential(s)
  - `key` alone, or `key` after api/secret/private/access/auth/client/signing/encryption/session/rsa/dsa/ec/ssh/pem/gpg/pgp/hmac/jwt/master/app/sign (e.g. `api_key`, `apikey`, `apiKey`, `FUTU_RSA_KEY`, `rsa_private_key`)
  - acc/acct/account followed by id/no/num/number at any position, or as the last token (`FUTU_ACC_ID`, `account-id`), plus `user_id` and `session_id`
- Space-separated values after password/passwd/pwd/pw/token/secret (`pwd hunter2`).
- Bare account numbers (4+ digits) after account/acc/acct (`account 28001234`).
- `Authorization:` / `Proxy-Authorization:` with any scheme (Bearer, Basic, token, Digest with all its params, NTLM, …): in header style the rest of the line is masked; in a quoted repr, the whole quoted value. Standalone `Bearer/Basic <token>` is masked too.
- `name=value` in URLs or query strings, even inside quoted reprs, and `scheme://user:pass@` userinfo.

## Not masked on purpose
These are tested: `passport`, `compass`, `bypass`, `monkey`, `keys`, `keyboard`, `account_balance`, `optionSessionOpen`, `symbol=…`, `status=…`. A token like `pass` is only matched when it's a whole token.

## Known unmasked cases (not covered)
- URL-encoded secrets: `password%3Dhunter2` and `%27`-quoted values aren't decoded.
- List/tuple forms where the label and value are separate items: `('password', 'hunter2')`, `['pwd', 'x']`.
- Prose with no `:`/`=`: `password is hunter2`. (`password hunter2` is masked; `pass hunter2` isn't, to avoid masking ordinary text like "tests pass".)
- Bare secrets with no label: a token or password printed alone.
- Handlers added **after** `configure()` runs: uvicorn `--reload` workers re-running dictConfig, the Futu SDK's `FTLog.fontColor()` (unused), libraries that add handlers lazily. **Don't use `--reload` in production.**
- Anything written straight to stdout/stderr rather than through `logging`, such as `print` or C-level output.

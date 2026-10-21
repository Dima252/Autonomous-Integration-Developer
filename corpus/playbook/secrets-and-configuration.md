# Keep secrets in the environment, never in code

API keys, OAuth tokens and webhook signing secrets grant access to customer data and
money. Once committed to a repository, a secret should be considered leaked: rotating
it is the only fix.

## Rules

- **Read secrets from environment variables** (or a secret manager that injects them).
  Fail fast at startup when a required one is missing, instead of on the first request.
- **Use typed settings.** `pydantic-settings` gives one validated object, and `SecretStr`
  keeps values out of reprs and logs.
- **Commit a `.env.example` with names and no values.** Keep `.env` in `.gitignore`.
- **Separate environments.** Stripe test keys (`sk_test_...`) and live keys
  (`sk_live_...`) are different, and so are webhook secrets per endpoint. Never point a
  development deployment at live credentials.
- **Least privilege.** Request only the OAuth scopes the integration needs (for example,
  Slack `chat:write` rather than broad admin scopes). Use restricted keys where the
  provider offers them (Stripe restricted keys, HubSpot private app scopes).
- **Never log secrets.** Don't log full request headers (they carry `Authorization` and
  signatures) or token values. Redact in one place.
- **Plan for rotation.** Read secrets at startup from config, not scattered
  `os.environ` calls, so changing one is a deploy, not a code change.

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    stripe_api_key: SecretStr
    stripe_webhook_secret: SecretStr
    slack_bot_token: SecretStr
    slack_alert_channel: str = "#billing-alerts"


settings = Settings()  # raises at startup if a required variable is missing
stripe_key = settings.stripe_api_key.get_secret_value()
```

## Tests to write

- Settings load from environment variables in tests: use test values, never real ones.
- No secret value appears in log output (capture logs and assert).

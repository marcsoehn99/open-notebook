# Configuration Guide

## Optional Mistral reasoning

Open Notebook does not send `reasoning_effort` unless you set one of the variables below. The default is `provider_default`, which leaves the field off the request. Chat, Ask, source chat, short prompts, transformations, and quizzes all start from that default.

Set `OPEN_NOTEBOOK_REASONING_<TASK>` for one task, or `OPEN_NOTEBOOK_REASONING_DEFAULT` for every task that does not have its own variable. Task names are `CHAT`, `ASK`, `SOURCE_CHAT`, `SHORT`, `TRANSFORMATION`, and `QUIZ`. Example: `OPEN_NOTEBOOK_REASONING_QUIZ=high`.

The API and the worker both read these variables. A value is mapped before it is sent:

| Value you set | Field sent |
| --- | --- |
| `none`, `minimal`, `low` | `none` |
| `medium`, `high` | `high` |
| `off`, `provider_default`, or unset | nothing |
| anything else | nothing, and a warning is logged |

Mistral documents `none` and `high` for adjustable reasoning. See [Reasoning](https://docs.mistral.ai/studio/conversations/reasoning). This project sends the field only when the provisioned model is Mistral and its name is `mistral-small-latest`, or starts with `mistral-large-4` or `mistral-medium-3-5`. Other providers ignore the variables. Other Mistral models ignore them too. The installed client does not say whether the API accepts `low`. `low` is sent as `none`.

Quiz generation does not change its temperature because of this setting.

**This file has moved for the rest of the configuration guide.**

All configuration documentation has been consolidated into the new documentation structure.

👉 **[Read the Configuration Guide](docs/5-CONFIGURATION/index.md)**

---

## Quick Links

- **AI Provider Setup** → [AI Providers](docs/5-CONFIGURATION/ai-providers.md)
- **Environment Variables Reference** → [Environment Reference](docs/5-CONFIGURATION/environment-reference.md)
- **Database Configuration** → [Database Setup](docs/5-CONFIGURATION/database.md)
- **Security Setup** → [Security Configuration](docs/5-CONFIGURATION/security.md)
- **Reverse Proxy** → [Reverse Proxy Setup](docs/5-CONFIGURATION/reverse-proxy.md)
- **Advanced Tuning** → [Advanced Configuration](docs/5-CONFIGURATION/advanced.md)

---

## What You'll Find

The new configuration documentation includes:

- **Complete environment variable reference** with examples
- **Provider-specific setup guides** for OpenAI, Anthropic, Google, Groq, Ollama, and more
- **Production deployment configurations** with security best practices
- **Reverse proxy examples** for Nginx, Caddy, Traefik
- **Database tuning** for performance optimization
- **Troubleshooting guides** for common configuration issues

---

For all configuration details, see **[docs/5-CONFIGURATION/](docs/5-CONFIGURATION/index.md)**.

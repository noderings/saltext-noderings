# Configuration

Every call needs a NodeRings service account token. Create one in the
NodeRings console under **Organization**, **Access Control**, **Service
accounts**: create the service account, give it a role, and generate a token.

## Execution and state modules

Put the token in the minion configuration, or better in pillar, under
`noderings`:

```yaml
noderings:
  token: <service account token>
  # optional
  organization_id: <organization id>   # act in another organization
  api_url: https://api.noderings.com
  timeout: 60                          # seconds per request
  retries: 5                           # retries of rate-limited and transient failures
```

Without `noderings:token`, the `NODERINGS_TOKEN` environment variable of the
minion process is used.

To manage two organizations, add a second key and pass it as `profile`:

```yaml
noderings_staging:
  token: <another token>
```

```bash
salt '*' noderings.list_vms profile=noderings_staging
```

## Salt Cloud

The provider block takes the same keys. See [Salt Cloud](cloud.md).

## Rate limits

The NodeRings API allows about two requests per second for each operation.
The extension retries rate-limited calls with backoff, so large state runs take
longer but still finish.

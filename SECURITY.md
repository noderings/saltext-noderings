# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Email **partners@noderings.com** with:

- a description of the issue and its impact
- steps to reproduce, or a proof of concept
- the extension version (`pip show saltext.noderings`) and Salt version (`salt --versions-report`)

We acknowledge reports within three working days and keep you updated until the issue is fixed. Credit is given in the release notes unless you ask us not to.

## Supported versions

Security fixes are made for the latest minor release.

## Handling credentials

The modules read the NodeRings service account token from the `noderings` profile in the minion or master configuration, from pillar, or from the `NODERINGS_TOKEN` environment variable. Keep the token in encrypted pillar (for example the GPG renderer) or a secrets backend rather than in plain configuration, and give the service account the narrowest NodeRings role that covers the resources you manage.

# CI on the NodeRings self-hosted runner

Pushes to `main` run CI on the NodeRings org runner `noderings-ci` (labels
`self-hosted`, `linux`, `x64`), the same runner the other NodeRings repositories use.
Each moved job has:

```yaml
runs-on: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && fromJSON('["self-hosted","linux","x64"]') || 'ubuntu-latest' }}
```

Everything else stays on GitHub-hosted runners:

- pull requests, including from forks, so untrusted code never runs on our machine;
- manual and scheduled runs;
- jobs that install system packages with `sudo`, which would change the shared machine;
- release and publish jobs, which handle signing keys and registry credentials.

The runner keeps state between jobs, so a job that starts containers must use names
unique to the run and remove them in an `if: always()` step.

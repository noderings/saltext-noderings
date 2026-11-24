# Installation

The extension needs Salt 3006 or newer. Install it on every minion that calls
the `noderings` execution or state module, and on the master for Salt Cloud.

With an onedir Salt installation (the default for Salt packages):

```bash
salt-pip install saltext-noderings
```

With Salt installed in a virtualenv or system Python:

```bash
pip install saltext-noderings
```

Check that Salt finds it:

```bash
salt-call --local sys.list_functions noderings
```

The extension has no dependencies beyond Salt.

# Dependency lock record

- Direct inputs: `requirements.in`
- Runtime lock: `requirements.txt`
- Runtime lock SHA-256:
  `0140aee1d6043da76c6c7e5089947e65c8151d33c92d5b1909af2e8db1c101a7`
- Test lock: `requirements-test.txt` (runtime lock only; `unittest` is in the
  standard library)
- Lock generated with: `pip-tools==7.6.0`
- Validation interpreter: `Python 3.13.5`

Regenerate from official PyPI metadata:

```sh
pip-compile --generate-hashes --no-header --strip-extras \
  --output-file=requirements.txt requirements.in
sha256sum requirements.txt
```

Update the digest above in the same commit. Production deployments log both
the actual venv Python version and lock digest, so host evidence does not depend
on this checkout's validation interpreter.

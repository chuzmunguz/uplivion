# Uplivion regression suite

These tests run against the real Flask app, using a temporary SQLite
database and upload folder created fresh for each run — nothing touches
real data. Most tests send actual HTTP requests through Flask and exercise
real JWTs, real bcrypt password hashing, and real SQLite transactions, so
they catch the same failures a live deployment would hit. A handful of
tests check the code or config directly instead, for things that are hard
to observe through a request.

Run the full suite:

```sh
UPLIVION_TEST_PYTHON=/path/to/venv/bin/python ./tests/run.sh
```

Point `UPLIVION_TEST_PYTHON` at a Python interpreter that has the packages
from `requirements-test.txt` installed. Running the tests won't leave
`.pyc` files behind in the checkout.

To set up that environment (and, separately, to refresh the pinned runtime
dependencies):

```sh
python3 -m venv /tmp/uplivion-test-venv
/tmp/uplivion-test-venv/bin/pip install --require-hashes -r requirements-test.txt
pip-compile --generate-hashes --no-header --strip-extras \
  --output-file=requirements.txt requirements.in
```

This suite can't verify everything by itself. Actually deploying nginx and
systemd, checking fail2ban against real production logs, and clicking
through the app in a browser still need to be checked by hand on a live
host.

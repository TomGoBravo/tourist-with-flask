# tourist-with-flask
An implementation of the Underwater Hockey Tourist using Flask. See the live site at https://pucku.org/tourist/.

# Quickstart with docker

Install docker on your machine.

Checkout git repo https://github.com/TomGoBravo/tourist-with-flask.git

Change into the directory containing `compose.yaml`.

Run `docker compose -f compose.yaml up flaskdebugrun` to start the server locally.


# Starting with flask running in a local venv

First install [pyenv](https://github.com/pyenv/pyenv). Don't miss [pyenv Common-build-problems](https://github.com/pyenv/pyenv/wiki/Common-build-problems). Then try the following which works in debian 11:

```
PYTHON_CONFIGURE_OPTS="--enable-loadable-sqlite-extensions" pyenv install 3.10.7
pyenv virtualenv 3.10.7 tourist-3.10.7
pyenv activate tourist-3.10.7
python -m pip install pip==21.3  # Work around for https://github.com/jazzband/pip-tools/issues/1639
python -m pip install pip-tools
pip-sync

# Run tests
python -m pytest tourist/tests/

# Setup a blank database
cp tourist/tests/spatial_metadata.sqlite tourist.db

# Import JSON lines file. Get from old system or in the test directory.
FLASK_APP=tourist flask sync import_jsonl tourist/tests/testentities.jsonl

# Start dev server
FLASK_ENV=development FLASK_APP=tourist flask run
```

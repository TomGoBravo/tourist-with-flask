# tourist-with-flask
An implementation of the Underwater Hockey Tourist using Flask. See the live site at https://pucku.org/tourist/.

# Quickstart

First install [pyenv](https://github.com/pyenv/pyenv). Don't miss [pyenv Common-build-problems](https://github.com/pyenv/pyenv/wiki/Common-build-problems). Then try the following which works in debian 9.0:

```
PYTHON_CONFIGURE_OPTS="--enable-loadable-sqlite-extensions" pyenv install 3.7.2
pyenv virtualenv 3.7.2 tourist-3.7.2
pyenv activate tourist-3.7.2
pip install -r requirements.txt

# Run tests
PYTHONPATH=. pytest tourist/tests/

# Setup a blank database
cp tourist/tests/spatial_metadata.sqlite tourist.db

# Import JSON lines file. Get from old system or in the test directory.
FLASK_APP=tourist flask sync import_jsonl tourist/tests/testentities.jsonl

# Start dev server
FLASK_ENV=development FLASK_APP=tourist flask run
```

# tourist-with-flask
Implementation of the Underwater Hockey Tourist using Flask

# Quickstart

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
FLASK_APP=tourist flask sync import_jsonl ../tourist/extractall.jsonl

# Start dev server
FLASK_ENV=development FLASK_APP=tourist flask run
```

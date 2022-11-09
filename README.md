# tourist-with-flask
An implementation of the Underwater Hockey Tourist using Flask. See the live site at https://pucku.org/tourist/.

# Quickstart with docker

If you want to run the frontend without changing any dependancies, for example to modify the
HTML or Javascript or make small changes to the Python code, it is probably easiest to run it in
container with the production release image.

To run a development frontend:
* Install docker on your machine.
* Checkout git repo <https://github.com/TomGoBravo/tourist-with-flask>.
* Change into the directory containing `compose.yaml`.
* Run `docker compose -f compose.yaml up flaskdebugrun` to start a container running flask on
  your machine.
* Go to <http://127.0.0.1:5001/tourist>.
* Modify the files you checked out from github in the `tourist/` directory. The docker
  container mounts this directory on your host machine inside the container. Depending on the
  change you may need to restart `flaskdebugrun` to see your change.

To run the prefect pipeline (there may be missing steps to do with the prefect configuration):
* Get the frontend running in docker
* Start a local copy of the orion server with `docker compose run prefectoriondevelopment --detach`
* Deploy the flows `PYTHONPATH=. python tourist/scripts/dataflow_deployment.py`
* Start a local agent with `docker compose -f compose.yaml up prefectagentdevelopment --detach`
* Make a new run of the flow using the UI at <http://127.0.0.1:4200>.


# Starting with flask running in a local venv

Do this if you are doing this like changing the dependancies or making a new docker image.

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

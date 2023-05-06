# tourist-with-flask
An implementation of the Underwater Hockey Tourist using Flask. See the live site at https://pucku.org/tourist/.

This page is for people who are interested in changing the code that runs the hockey tourist.
If you'd like to improve our list of clubs please read <https://pucku.org/tourist/about>.


## Run your own instance in the cloud

You can now run and modify your own instance entirely in the cloud! When logged into github go
to https://github.com/TomGoBravo/tourist-with-flask/ and look for the green `<> Code` button.
Click on it, then `Create codespace`. After about 20 seconds there should be terminal with a prompt
that looks like `root@codespaces-60d6e9:/workspaces/tourist-with-flask# `. Run `flask --debug 
run`, then look for the green `Open in browser` button. When that opens you are connected to 
your own private instance of the tourist. Explore and modify the source in the left panel, this 
is a safe place to experiment.


## Run a frontend and prefect instance locally with docker

If you can't use codespace to run an instance in the cloud, you can run the same container on 
your local machine.

* Install docker on your machine.
* Checkout the git repo <https://github.com/TomGoBravo/tourist-with-flask>.
* Run `docker compose -f .devcontainer/compose.yaml up` to start 3 containers: flask, the prefect
  agent and prefect server.
* Visit <http://127.0.0.1:5001/tourist/> to browse the flask server.
* Run tests in the container with `docker compose -f .devcontainer/compose.yaml run flaskdebugrun python -m pytest tourist/tests`
* To deploy the prefect flow run `docker compose -f .devcontainer/compose.yaml run prefectagentdev flask sync deploy-dataflow`
* Visit <http://127.0.0.1:4200> and run the flow you just deployed.
* Modify the files you checked out from github in the `tourist/` directory. The docker
  container mounts this directory on your host machine inside the container. Some changes 
  trigger an automatic reload, others may need you to restart the container.


## Run flask in a local venv

Use this method if you are changing the dependencies or manually making a docker image.

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

./.devcontainer/setup-dev-data.sh

# Start dev server
FLASK_APP=tourist flask --debug run
```

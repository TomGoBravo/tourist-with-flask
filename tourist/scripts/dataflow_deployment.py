import pathlib

from prefect.infrastructure import DockerContainer

import tourist.scripts.dataflow
import tourist.config
from prefect.deployments import Deployment

dev_data_path = pathlib.Path(__file__).parent.parent.parent / "dev-data"

Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_dev_data",

    infra_overrides={"env.DATA_DIR": str(dev_data_path),
                     "env.FLASK_ENV": "development"},
    work_queue_name="test",
).apply()


container = DockerContainer(image='tomgobravo/tourist-with-flask:tourist-docker')
Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_dockerhub_development",
    infrastructure=container,
    infra_overrides={"env.DATA_DIR": "/data",
                     "env.FLASK_ENV": "development",
                     "volumes": ['/home/thecap/code/tourist-with-flask/dev-data:/data']},
    work_queue_name="development",
    path="/app",
).apply()


container = DockerContainer(image='tomgobravo/tourist-with-flask:tourist-docker')
Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_production",
    infrastructure=container,
    infra_overrides={"env.DATA_DIR": "/data",
                     "env.FLASK_ENV": "production",
                     "volumes": ['/var/local/www-data:/data']},
    work_queue_name="development",
    path="/app",
).apply()

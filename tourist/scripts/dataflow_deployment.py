import pathlib

import tourist.scripts.dataflow
import tourist.config
from prefect.deployments import Deployment
from prefect.filesystems import LocalFileSystem

dev_data_path = pathlib.Path(__file__).parent.parent.parent / "dev-data"

run_gb_deployment_dev_data = Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_dev_data",

    infra_overrides={"env.DATA_DIR": str(dev_data_path),
                     "env.FLASK_ENV": "development"},
    work_queue_name="test",
)
run_gb_deployment_dev_data.apply()


run_gb_deployment_production = Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_production",
    infra_overrides={"env.FLASK_ENV": "production"},
    work_queue_name="production",
    storage=LocalFileSystem(basepath="/var/local/www-data/tourist-prefect-agent/deployment/run_gb_fetch_and_sync_production"),
)
run_gb_deployment_production.apply()

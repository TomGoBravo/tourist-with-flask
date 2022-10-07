import pathlib

import tourist.scripts.dataflow
import tourist.config
from prefect.deployments import Deployment


Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_dev_data",
    infra_overrides={"env.DATA_DIR": "/data",
                     "env.FLASK_ENV": "development"},
    work_queue_name="development",
    path='/app',
).apply()


Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync_production",
    infra_overrides={"env.DATA_DIR": "/data",
                     "env.FLASK_ENV": "production"},
    work_queue_name="production",
    path='/app',
).apply()

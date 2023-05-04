import pathlib

import tourist.scripts.dataflow
import tourist.config
from prefect.deployments import Deployment


Deployment.build_from_flow(
    flow=tourist.scripts.dataflow.run_gb_fetch_and_sync,
    name="run_gb_fetch_and_sync",
    work_queue_name="development",
    path='/workspace/tourist-with-flask',
).apply()
import prefect
from prefect import flow

from tourist.models import tstore
from tourist.scripts import scrape
from prefect.task_runners import SequentialTaskRunner

import logging


logging.captureWarnings(True)


@flow()
def run_fetch(source: scrape.Source):
    scrape.fetch(scrape.make_session_from_flask_config(), [source])


@flow(task_runner=SequentialTaskRunner())
def run_gb_fetch_and_sync():
    logger = prefect.get_run_logger()
    logger.info("Starting fetch and sync for gbuwh-feed-clubs")

    gbsource = scrape.SOURCE_BY_SHORT_NAME['gbuwh-feed-clubs']
    new_fetch = scrape.fetch_one(gbsource)

    sstore_session = scrape.make_session_from_flask_config()
    scrape.add_url_fetch_to_session(sstore_session, new_fetch)
    sstore_session.commit()

    uk_place = tstore.Place.query.filter_by(short_name='uk').one()
    scrape.parse_and_extract_gbfeed(uk_place, new_fetch)

    logger.info("Finished fetch and sync for gbuwh-feed-clubs")


@flow()
def run_all_fetches():
    for source in scrape.SOURCES:
        run_fetch(source)






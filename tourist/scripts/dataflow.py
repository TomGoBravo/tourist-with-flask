import os
from typing import Optional
from typing import Union

import prefect
from prefect import flow

import tourist
from tourist.models import sstore
from tourist.models import tstore
from tourist.scripts import scrape
from prefect.task_runners import SequentialTaskRunner

import logging


logging.captureWarnings(True)


@flow()
def run_fetch(source: scrape.Source):
    scrape.fetch(scrape.make_session_from_flask_config(), [source])


@flow(task_runner=SequentialTaskRunner())
def run_gb_fetch_and_sync(fetch_timestamp: Optional[str] = None):
    app = tourist.create_app()

    with app.app_context():
        logger = prefect.get_run_logger()
        logger.info("Starting fetch and sync for gbuwh-feed-clubs")

        gbsource = scrape.SOURCE_BY_SHORT_NAME['gbuwh-feed-clubs']

        with scrape.make_session_from_flask_config() as sstore_session:
            if not fetch_timestamp:
                fetch = scrape.fetch_one(gbsource)

                scrape.add_url_fetch_to_session(sstore_session, fetch)
                sstore_session.commit()
            else:
                fetch = sstore_session.query(sstore.UrlFetch).filter_by(
                    created_timestamp=fetch_timestamp).one()

            uk_place = tstore.Place.query.filter_by(short_name='uk').one()
            scrape.parse_and_extract_gbfeed(uk_place, fetch)

        logger.info("Finished fetch and sync for gbuwh-feed-clubs")


@flow()
def run_all_fetches():
    for source in scrape.SOURCES:
        run_fetch(source)






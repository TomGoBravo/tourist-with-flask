#!/bin/bash

mkdir dev-data
mkdir logs

# Setup a blank database
cp tourist/tests/spatial_metadata.sqlite dev-data/tourist.db

# Import JSON lines file. Get from old system or in the test directory.
FLASK_APP=tourist TOURIST_ENV=development flask --debug sync import_jsonl tourist/tests/testentities.jsonl

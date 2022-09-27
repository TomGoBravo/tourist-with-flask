# Inspired by https://blog.logrocket.com/build-deploy-flask-app-using-docker/ and https://medium.com/swlh/flask-docker-the-basics-66a699aa1e7d


# start by pulling the python image
FROM python:3.10.7-slim-buster

# copy the requirements file into the image
COPY ./requirements.txt /app/requirements.txt

# switch working directory
WORKDIR /app

# Install git because some packages in requirements need it.
RUN apt-get update
RUN apt-get install --yes git libsqlite3-mod-spatialite gcc

# install the dependencies and packages in the requirements file.
# install uwsgi with pip to get 2.0.20 instead of the 2.0.18 (that apt-get
# has, released in 2019 and doesn't support the 'module' ini directive).
RUN pip install -r requirements.txt uwsgi

# Save some space by removing gcc and the apt cache
RUN apt-get purge --yes gcc
RUN apt-get autoremove --yes
RUN apt-get clean autoclean

# copy the app to the image
COPY uwsgi.ini /app
COPY tourist /app/tourist

ENV FLASK_APP=tourist
ENV DATA_DIR=/data
ENV LOG_DIR=/data

# Entry CMD is set by the compose.yaml service command.

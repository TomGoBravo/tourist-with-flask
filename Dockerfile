# Inspired by https://blog.logrocket.com/build-deploy-flask-app-using-docker/ and https://medium.com/swlh/flask-docker-the-basics-66a699aa1e7d

# start by pulling the python image
FROM python:3.10.7-buster AS builder

# Install git because some packages in requirements need it.
RUN apt-get update && apt-get install --yes git gcc libsqlite3-mod-spatialite

COPY requirements.txt uwsgi.ini /app/
# install the dependencies and packages in the requirements file.
# install uwsgi with pip to get 2.0.20 instead of the 2.0.18 (that apt-get
# has, released in 2019 and doesn't support the 'module' ini directive).
RUN pip install -r /app/requirements.txt uwsgi

# Copy tourist source separately from other other files because it changes more often. This
# increases the chances of the cached layer created by `RUN pip` being used.
COPY tourist /app/tourist
RUN chmod --recursive a+r /app

ENV FLASK_APP=tourist
ENV TOURIST_ENV=development



FROM python:3.10.7-slim-buster AS production

# Inspecting the docker layers created above with wagoodman/dive shows the output from them
# needed for running production is
COPY --from=builder /app /app
COPY --from=builder /usr/local /usr/local

# Copying all the libs from builder is tricky because spatialite has quite a few transitive
# dependencies, Dockerfile COPY copies symlink targets instead of the symlink (which
# causes a ldconfig warning) and load_extension('.../mod_spatialite.so') in create_app tries to load
# '.../mod_spatialite.so.so' for a reason I couldn't work out. So instead of COPY --from=builder ...
# this uses apt-get to install libsqlite3-mod-spatialite, which works reliably.
RUN apt-get update && apt-get install --yes libsqlite3-mod-spatialite && apt-get clean autoclean \
    && rm -fr /var/lib/apt/lists \

ENV FLASK_APP=tourist
ENV TOURIST_ENV=production


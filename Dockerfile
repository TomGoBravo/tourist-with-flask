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
# increases the chances of the cached pip layer being used.
COPY tourist /app/tourist
RUN chmod --recursive a+r /app

# Save some space by removing gcc and the apt cache
# Disabled while making a devcontainer.
# RUN apt-get purge --yes gcc && apt-get autoremove --yes && apt-get clean autoclean

ENV FLASK_APP=tourist

# Entry CMD is set by the compose.yaml service command.


# The following would make a nice slim production image but the app fails to load due to:
# File "/app/tourist/__init__.py", line 87, in load_spatialite
#   dbapi_conn.load_extension('/usr/lib/x86_64-linux-gnu/mod_spatialite.so')
# sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) /usr/lib/x86_64-linux-gnu/mod_spatialite.so.so: cannot open shared object file: No such file or di
#
# I'm guessing this is something to do with ldconfig not updating /etc correctly because the
# /usr/lib paths are the same as the build image above.

# FROM python:3.10.7-slim-buster AS production
#
# # Inspecting the docker layers created above with wagoodman/dive shows the output from them
# # needed for running production is
# COPY --from=builder /app /app
# COPY --from=builder /usr/local /usr/local
# COPY --from=builder /usr/lib/x86_64-linux-gnu/libfreexl.so* /usr/local/x86_64-linux-gnu/
# COPY --from=builder /usr/lib/x86_64-linux-gnu/libgeos* /usr/local/x86_64-linux-gnu/
# COPY --from=builder /usr/lib/x86_64-linux-gnu/libproj.so* /usr/local/x86_64-linux-gnu/
# COPY --from=builder /usr/lib/x86_64-linux-gnu/mod_spatialite.so* /usr/local/x86_64-linux-gnu/
# COPY --from=builder /usr/share/proj /usr/share/proj
# RUN ldconfig
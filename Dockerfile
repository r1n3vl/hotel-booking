FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DJANGO_DEBUG=false
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install -r requirements.txt && adduser --system --group --uid 10001 app
COPY --chown=app:app manage.py /app/manage.py
COPY --chown=app:app config /app/config
COPY --chown=app:app accounts /app/accounts
COPY --chown=app:app hotels /app/hotels
COPY --chown=app:app bookings /app/bookings
COPY --chown=app:app docker /app/docker
RUN mkdir /app/staticfiles && chown app:app /app/staticfiles
USER app
EXPOSE 8000
ENTRYPOINT ["python", "docker/start.py"]

FROM runtime AS test
USER root
COPY requirements-dev.txt pyproject.toml /app/
COPY --chown=app:app tests /app/tests
RUN python -m pip install -r requirements-dev.txt && chown app:app /app
USER app
ENTRYPOINT []
CMD ["python", "-m", "pytest", "--cov", "--cov-report=term-missing"]

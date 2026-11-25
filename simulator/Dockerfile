FROM python:3.13.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY sim ./sim

# .state keeps the bootIds; a new volume mounted here takes this folder's owner.
RUN useradd --create-home --uid 10001 sim && install -d -o sim -g sim .state
USER sim

CMD ["python", "-m", "sim.main"]

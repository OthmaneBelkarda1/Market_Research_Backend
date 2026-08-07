# Image officielle Playwright : Chromium et toutes ses dependances systeme sont deja la.
# C'est ce qui evite le `playwright install --with-deps`, qui exige root et echoue sur le
# runtime natif de Render. La version suit `playwright` dans `uv.lock` : les deux doivent
# bouger ensemble, sinon le driver refuse le navigateur pre-installe.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Les dependances avant le code : cette couche n'est reconstruite que si le lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Le venv d'abord dans le PATH : plus besoin de prefixer par `uv run`.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# `$PORT` est injecte par Render et doit etre respecte, d'ou la forme shell.
# Un seul worker, jamais `--reload` : les etudes vivent dans la memoire du processus et
# sont protegees par un semaphore in-process (voir README, "Un seul worker uvicorn").
CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}

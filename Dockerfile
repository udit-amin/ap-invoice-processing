# One image, two roles. The same image runs the API (default CMD) and the UI
# (command overridden in render.yaml / docker run). Mirrors docs/OPERATIONS.md's
# "four runtime pieces, all from one container image".
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencies first for layer caching. psycopg[binary], PyMuPDF, pdfplumber and
# Pillow ship manylinux wheels, so no system build toolchain is needed; if a
# future dep needs to build from source, add `build-essential libpq-dev` here.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Documentation only — Render/compose inject $PORT and override the command.
EXPOSE 8000

# Default role: the API. Render's ap-ui service overrides this with the
# `streamlit run ui/app.py …` command (see render.yaml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

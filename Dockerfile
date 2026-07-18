FROM python:3.13-slim

WORKDIR /app

# Install pinned runtime deps first — reproducible builds (an upstream release
# can't change what ships) and a cached layer that only busts when the lock
# changes. Then install the app itself without re-resolving deps.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

CMD ["python", "-m", "tcdd_bot.main"]

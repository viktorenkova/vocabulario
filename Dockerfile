FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY vocabulario ./vocabulario
RUN pip install --no-cache-dir .
COPY data/dictionary.jsonl data/ATTRIBUTION.md ./data/
COPY data/licenses ./data/licenses
COPY data/images ./data/images
RUN mkdir runtime && chown -R 10001:10001 /app
USER 10001:10001
CMD ["python", "-m", "vocabulario.bot"]

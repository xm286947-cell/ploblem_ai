FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PKR_DATA_DIR=/var/lib/public-knowledge
WORKDIR /app
COPY public_knowledge_rag/requirements-runtime.txt ./requirements-runtime.txt
RUN pip install --no-cache-dir -r requirements-runtime.txt
RUN mkdir -p /app/public_knowledge_rag
COPY public_knowledge_rag/*.py public_knowledge_rag/*.html ./public_knowledge_rag/
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /var/lib/public-knowledge \
    && chown -R appuser:appuser /app /var/lib/public-knowledge
USER appuser
EXPOSE 8080
CMD ["uvicorn", "public_knowledge_rag.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

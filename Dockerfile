FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/ backend/
RUN pip install --no-cache-dir "./backend[llm]"
COPY --from=ui /ui/dist frontend/dist
ENV DISTILL_DATABASE_URL=sqlite:////data/distill.db
VOLUME /data
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "distill.api:app", "--host", "0.0.0.0", "--port", "8000"]

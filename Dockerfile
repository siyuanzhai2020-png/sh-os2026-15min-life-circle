FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python tools/build_demo.py --rebuild

EXPOSE 8000
CMD ["python", "tools/serve.py", "--host", "0.0.0.0", "--port", "8000", "--warm"]
